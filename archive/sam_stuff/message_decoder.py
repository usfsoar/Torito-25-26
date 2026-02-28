import struct

class FrameDecoder:
    
    def __init__(self, num_sensors=4):
        self.num_sensors = num_sensors
        self.STRUCT_FORMAT = f"<IIBBH{num_sensors}H"

    def _decode(self):
        try:
            unpacked = struct.unpack(self.STRUCT_FORMAT, self.data_bytes)
        except struct.error as e:
            print(f"Error unpacking data: {e}")
            return
        self.timestamp_us = unpacked[0]
        self.seq = unpacked[1]
        self.valid_mask = unpacked[2]
        self.status_bits = unpacked[3]
        self.solenoid_state = unpacked[4]
        self.raw_adc = list(unpacked[5:])

    def timestamp_s(self):
        return self.timestamp_us / 1_000_000.0

    def human_time(self):
        minutes = int(self.timestamp_s() // 60)
        seconds = self.timestamp_s() % 60
        return f"{minutes:02d}:{seconds:06.3f}"

    def decoder(self, msg):
        self.data_bytes = bytes.fromhex(msg)
        self._decode()
        return self.timestamp_s(), self.seq, self.valid_mask, self.status_bits, self.solenoid_state, self.raw_adc
# msg = "40083B2406180000000200000000000000000000"
# frame = FrameDecoder()

# seconds, seq, mask, status, solenoid_state, adcs = frame.decoder(msg)

# print(f"Time: {seconds}s")
# print(f"Sequence: {seq}")
# print(f"Valid Mask: {mask}")
# print(f"Status Bits: {status}")
# print(f"Solenoid State: {solenoid_state}")
# print(f"ADCs: {adcs}")