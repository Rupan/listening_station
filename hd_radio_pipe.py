#!/usr/bin/env python3

from gnuradio import gr, blocks, filter
import osmosdr

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
