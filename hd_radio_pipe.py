#!/usr/bin/env python3

from gnuradio import gr, blocks, filter
import osmosdr

"""
Amplifier settings:

LNA (Low Noise Amplifier) is the first gain stage right at the antenna input - it
amplifies the weak incoming RF signal before it hits the mixer. You want just enough
LNA gain to lift signals above the noise floor without overloading the front end.
Too much and strong signals will clip and create spurious images across the waterfall.

VGA (Variable Gain Amplifier) is the baseband/IF gain stage that comes after the mixer
has downconverted the signal. It amplifies the intermediate signal before it hits the
ADC. This is where you adjust the overall signal level to fill the ADC's dynamic range
without clipping.

The general approach: start with LNA low and VGA mid-range, then bring up the LNA until
you can see the signals you're interested in. If the noise floor rises too much or you
see phantom signals appearing, back off the LNA and compensate with VGA instead. LNA gain
is "expensive" in terms of noise and overload risk; VGA gain is "cheaper" but can't
recover signals that were lost in the noise before the mixer.

Conservative starting values were selected that wouldn't overload the HackRF front end:
* RF amp off (0) - the HackRF's RF amp adds 14dB in one step and can easily overload on
  strong FM broadcast signals. 105.7 is your strongest station, so leaving it off avoids
  clipping.
* IF/LNA at 32 - mid-range of the 0-40dB range. Enough gain to get a good signal level
  without saturating. (0-40dB in 8dB steps)
* BB/VGA at 20 - low-to-mid in the 0-62dB range. Conservative, lets the ADC fill without
  clipping. (0-62dB in 2dB steps)

Sample rate:

2.016 MHz was chosen because it's close to the HackRF's minimum (2 MHz) and the ratio
to 1,488,375 Hz reduces to a clean fraction (3969/5376).  Lower sample rate means less
data throughput over USB and less CPU work in the resampler.
"""

class HDRadioPipeline(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self)

        # HackRF source at 2.016 MHz (clean multiple for resampling)
        self.source = osmosdr.source(args="hackrf=0")
        self.source.set_sample_rate(2016000)
        self.source.set_center_freq(105.7e6)
        self.source.set_gain(0, 'RF')
        self.source.set_gain(32, 'IF')
        self.source.set_gain(20, 'BB')

        # Rational resampler: 2016000 -> 1488375
        # GCD(2016000, 1488375) = 375
        # 2016000 / 375 = 5376
        # 1488375 / 375 = 3969
        self.resampler = filter.rational_resampler_ccc(
            interpolation=3969,
            decimation=5376,
            taps=[],
            fractional_bw=0)

        # Convert complex float to unsigned byte (cu8)
        # Scale, offset by 127, clip to 0-255
        self.float_to_char = blocks.float_to_uchar()
        self.complex_to_float = blocks.complex_to_float()
        self.add_const_i = blocks.add_const_ff(127)
        self.add_const_q = blocks.add_const_ff(127)
        self.multiply_i = blocks.multiply_const_ff(127)
        self.multiply_q = blocks.multiply_const_ff(127)
        self.float_to_uchar_i = blocks.float_to_uchar()
        self.float_to_uchar_q = blocks.float_to_uchar()
        self.interleave = blocks.interleave(gr.sizeof_char, 1)

        # File sink to FIFO
        self.sink = blocks.file_sink(gr.sizeof_char, "/tmp/hd_radio.pipe")

        # Connect
        self.connect(self.source, self.resampler, self.complex_to_float)
        self.connect((self.complex_to_float, 0), self.multiply_i, self.add_const_i, self.float_to_uchar_i, (self.interleave, 0))
        self.connect((self.complex_to_float, 1), self.multiply_q, self.add_const_q, self.float_to_uchar_q, (self.interleave, 1))
        self.connect(self.interleave, self.sink)

if __name__ == "__main__":
    tb = HDRadioPipeline()
    tb.start()
    input("Press Enter to stop...")
    tb.stop()
    tb.wait()
