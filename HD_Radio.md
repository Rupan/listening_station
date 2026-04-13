## HD Radio Decoding

The `nrsc5` decoder only natively supports RTL-SDR hardware. To use it with the HackRF Pro, `hd_radio_pipe.py` bridges the gap using GNU Radio:

1. Captures IQ from the HackRF at 2.016 MHz sample rate via `osmosdr`
2. Resamples down to 1,488,375 Hz (the rate `nrsc5` expects for cu8 input)
3. Converts complex float IQ to unsigned 8-bit (cu8) format
4. Pipes the samples to `nrsc5` via a named FIFO

### Usage

```bash
# Create the FIFO (one time)
mkfifo /tmp/hd_radio.pipe

# Terminal 1: Start the GNU Radio capture pipeline
python3 hd_radio_pipe.py

# Terminal 2: Start the HD Radio decoder
path/to/nrsc5 -r /tmp/hd_radio.pipe 0
```

The last argument to `nrsc5` is the program number (0-3) for stations with multiple HD channels. To change stations, edit the center frequency in `hd_radio_pipe.py` and restart.

### Dependencies

```bash
sudo apt install gnuradio gr-osmosdr
```

`nrsc5` must be built from source: https://github.com/theori-io/nrsc5
