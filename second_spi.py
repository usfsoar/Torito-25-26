import spidev
import time

spi = spidev.SpiDev()
spi.open(0,0)
spi.max_speed_hz = 5000
spi.mode = 0

try:
	while True:
		to_send = [1,2,3,4]
		received = spi.xfer2(to_send)
		print(f"sent: {to_send} | Received: {received}")
		time.sleep(1)
except KeyboardInterrupt:
	spi.close()
