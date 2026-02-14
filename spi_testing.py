import spidev
import time

spi= spidev.SpiDev()
spi.open(0,0)

spi.max_speed_hz = 1000000
spi.mode = 0
def send_data(message_list):
	response = spi.xfer2(list(message_list))
	return response

try:
	while True:
		data_to_send = [0x01,0x02,0x03,0x04]
		print(f"sending: {data_to_send}")
		received = send_data(data_to_send)
		print(f"sent: {data_to_send}")
		print(f"Received from ESP32: {received}")

		time.sleep(1)

except KeyboardInterrupt:
	spi.close()
	print("SPI Closed")
