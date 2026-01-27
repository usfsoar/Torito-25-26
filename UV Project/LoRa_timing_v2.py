
#Created by J. Huang
import serial
import time
import jetson_lora_script as LoRaTransceiver

lora = LoRaTransceiver.LoRaTransceiver('/dev/ttyTHS1', 115200)
lora.configure_module()

def main():
    startTime = time.time_ns()/1000000
    #lora.send_message("C","7")
    test = "AT+SEND=7,1,C\r\n"
    lora.ser.write(test.encode("utf-8"))
    line = ""
    while(1):
        if(lora.ser.in_waiting > 0):
            line = lora.ser.readline().decode('utf-8', errors='ignore').strip()

        if line.startswith("+RCV="):
            parts = line.split(',')
            if len(parts) >= 3:
                sender_addr = parts[0].split('=')[1]
                msg_len = parts[1]
                content = parts[2]
            if parts[2] == "C":
                endTime = time.time_ns()/1000000
                et = endTime - startTime
                print(f"Round-trip time: {et} ms")
                print("break")
                break

main()
