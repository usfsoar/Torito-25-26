from smbus2 import SMBus

bus = SMBus(1)
address = 0x08

def send_data(message):
	data = [ord(c) for c in message]
	bus.write_i2c_block_data(address,0,data)

def request_data():
	data = bus.read_i2c_block_data(address,0,12)
	return "".join(chr(i) for i in data)

try:
	send_data("Ethan LOVES femboys!!!!")
	print("Received: ",request_data())
finally:
	bus.close()
