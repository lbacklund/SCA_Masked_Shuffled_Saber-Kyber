#!/usr/bin/env python
# coding: utf-8

import subprocess
import os
import sys
import time
import chipwhisperer as cw
from chipwhisperer.common.traces import Trace
import numpy as np
from tqdm import tqdm
import matplotlib.pyplot as plt
import fnmatch

###########################################

SETUP = "Masked Shuffled Kyber"
REPROGRAM = True

########

SAVED_SETUPS = {
    "Masked Shuffled Kyber": {
        "hex_path": "./hexes/bit_shuffled_masked_kyber_shares.hex",
        "short_delay": 0.3,
        "long_delay": 0.2,
        "repeat_traces": 2000,
        "num_of_samples": 96000,
        "samples_per_cycle": "clkgen_x1",
        "decimate_samples": 1,
        "presamples": 200,
        "supersampling": 1,
        "output_bytes": 320,
        },
}


###########################################

HEX_PATH = SAVED_SETUPS[SETUP]["hex_path"]

SHORT_DELAY = SAVED_SETUPS[SETUP]["short_delay"]
LONG_DELAY = SAVED_SETUPS[SETUP]["long_delay"]

REPEAT_TRACES = SAVED_SETUPS[SETUP]["repeat_traces"]
NUM_OF_SAMPLES = SAVED_SETUPS[SETUP]["num_of_samples"]
SAMPLES_PER_CYCLE = SAVED_SETUPS[SETUP]["samples_per_cycle"]
DECIMATE_SAMPLES = SAVED_SETUPS[SETUP]["decimate_samples"]
PRESAMPLES = SAVED_SETUPS[SETUP]["presamples"]
SUPERSAMPLING = SAVED_SETUPS[SETUP]["supersampling"]

PROJECT_NAME = "{}_x3_d{}_p{}_s{}".format(SETUP, DECIMATE_SAMPLES, PRESAMPLES, NUM_OF_SAMPLES)

OUTPUT_BYTES = SAVED_SETUPS[SETUP]["output_bytes"]

scope = cw.scope(type=cw.scopes.OpenADC)

target = cw.target(scope, cw.targets.SimpleSerial)

STM32_HSE = 24000000*SUPERSAMPLING

scope.default_setup()
scope.clock.clkgen_freq = STM32_HSE
scope.clock.freq_ctr_src = "clkgen"
scope.clock.adc_src = "clkgen_x1"
scope.adc.samples = NUM_OF_SAMPLES
scope.adc.decimate = DECIMATE_SAMPLES
scope.adc.offset = 0
scope.adc.presamples = PRESAMPLES

time.sleep(0.25)

print(scope.clock.adc_freq)
print(scope.clock.adc_rate)
print(scope.clock.freq_ctr_src)
print(scope.clock.clkgen_src)
print(scope.clock.clkgen_mul)
print(scope.clock.clkgen_div)

print("Timeout is          :",scope.adc.timeout)
print("Output Clock is     :",scope.clock.freq_ctr/1000000,"MHz")
print("ADC Clock is        :",scope.clock.adc_freq/1000000,"MHz")
print("Sampling Freq is    :",scope.clock.adc_freq/STM32_HSE)
print("ADC_PLL locked      :",scope.clock.adc_locked)
print("ADC Capture Samples :",scope.adc.samples)
print("ADC Decimate        :",scope.adc.decimate)
print("Trigger Pin         :",scope.trigger.triggers)
print("Trigger States      :",scope.io.tio_states)


if REPROGRAM:
    cw.program_target(scope, cw.programmers.STM32FProgrammer, HEX_PATH)

TracePath = "traces/shares/"

target.output_len = OUTPUT_BYTES

msg = bytearray([1]*1)

target.simpleserial_write('g', msg)

# Ask stm32 to send Secret Key
time.sleep(0.1)
target.simpleserial_write('s', msg)

sk = target.simpleserial_read('s', 64, timeout=1250,ack=False)
time.sleep(0.01)
for x in range(0,36):
    sk.extend(target.simpleserial_read('s', 64, end='\n', timeout=1250, ack=False))
    time.sleep(0.01)
sk = target.simpleserial_read('s', 32, timeout=1250,ack=False)

len(sk)

# Ask stm32 to send Public Key

time.sleep(0.01)
target.simpleserial_write('t', msg)

pk = target.simpleserial_read('t', 64, timeout=1250,ack=False)
time.sleep(0.01)
for x in range(0,17):
    pk.extend(target.simpleserial_read('t', 64, timeout=1250,ack=False))
    time.sleep(0.01)
pk.extend(target.simpleserial_read('t', 32, timeout=1250,ack=False))

len(pk)

time.sleep(1)

def capture_trace_kalle(scope, target, plaintext, key=None, ack=True):
    import signal, logging

    # useful to delay keyboard interrupt here,
    # since could interrupt a USB operation
    # and kill CW until unplugged+replugged
    class DelayedKeyboardInterrupt:
        def __enter__(self):
            self.signal_received = False
            self.old_handler = signal.signal(signal.SIGINT, self.handler)

        def handler(self, sig, frame):
            self.signal_received = (sig, frame)
            logging.debug('SIGINT received. Delaying KeyboardInterrupt.')

        def __exit__(self, type, value, traceback):
            signal.signal(signal.SIGINT, self.old_handler)
            if self.signal_received:
                self.old_handler(*self.signal_received)
    with DelayedKeyboardInterrupt():
        if key:
            target.set_key(key, ack=ack)

        scope.arm()

        if plaintext:
            target.simpleserial_write('p', plaintext)

        ret = scope.capture()

        i = 0
        while not target.is_done():
            i += 1
            time.sleep(0.05)
            if i > 100:
                warnings.warn("Target did not finish operation")
                return None

        if ret:
            warnings.warn("Timeout happened during capture")
            return None

        response = target.simpleserial_read('r', target.output_len, ack=ack,timeout=1000)
        wave = scope.get_last_trace()

    if len(wave) >= 1:
        return Trace(wave, plaintext, response, key)
    else:
        return None

def getTraces():
    file_number = -1
    for file_name in os.listdir(TracePath):
        if fnmatch.fnmatch(file_name, "traces_[0-9]*.npy"):
            n = int(file_name[7:-4])
            if n > file_number: file_number = n
    file_number += 1
    print("file_number =", file_number)

    shuffle_traces = np.zeros(shape=(REPEAT_TRACES, 14000))
    message_traces = np.zeros(shape=(REPEAT_TRACES, 59000))
    shuffle_labels = np.zeros(shape=(REPEAT_TRACES, 256))
    message_labels = np.zeros(shape=(REPEAT_TRACES, 64))
    
    for j in tqdm(range(0,REPEAT_TRACES), "Capturing traces"):
        # Write a random ciphertext
        target.simpleserial_write('e', msg)
        time.sleep(SHORT_DELAY)
        
        while True:
            try:
                trace = capture_trace_kalle(scope, target, msg, ack=False)
                trig = scope.adc.trig_count
                time.sleep(LONG_DELAY) # LINUS
                if trig >= 13615 or trig <= 13595: continue
                if trace == None: continue
                if trace.textout == None: continue
                shuffle_traces[j] = trace.wave[:14000]
                message_traces[j] = trace.wave[trig+7500:trig+66500]
                shuffle_labels[j] = trace.textout[:256]
                message_labels[j] = trace.textout[256:]
                break
            except Exception as e:
                print("Exception occured for trace", j)
                raise(e)
    
    print("Saving traces")
    np.save(TracePath+"shuffle_traces_"+str(file_number), shuffle_traces)
    np.save(TracePath+"message_traces_"+str(file_number), message_traces)
    np.save(TracePath+"shuffle_labels_"+str(file_number), shuffle_labels)
    np.save(TracePath+"message_labels_"+str(file_number), message_labels)

    target.close()

if __name__ == "__main__":
    getTraces()