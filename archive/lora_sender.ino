//#include <SoftwareSerial.h>
#include <HardwareSerial.h>
#define RX 3  // Connect to LoRa TX
#define TX 2  // Connect to LoRa RX

HardwareSerial lora(1);
//SoftwareSerial lora(RX, TX);
bool reporting_lock = false;
String lora_input = "";
String address = "";
unsigned long sendStartTime = 0;
bool waitingForReply = false;

void setup() {
  Serial.begin(9600);
  delay(100);
  
  lora.begin(115200, SERIAL_8N1, RX, TX);
  delay(1000);
  
  Serial.println("Initializing LoRa Sender...");
  
  // Verify module is responding
  if (sendATcommand("AT", 1000).indexOf("OK") != -1) {
    Serial.println("LoRa module responding");
  } else {
    Serial.println("WARNING: LoRa module not responding!");
  }
  
  sendATcommand("AT+ADDRESS=2", 1000);
  delay(100);
  sendATcommand("AT+BAND=915000000", 1000);
  delay(100);
  sendATcommand("AT+NETWORKID=18", 1000);
  delay(200);
  sendATcommand("AT+PARAMETER=5,9,1,4", 1000);
  delay(200);
  
  Serial.println("Setup complete. Format: <command>,<address> or <command>,<address>:repeat");
  Serial.println("Example: Hello,7");
}

void loop() {
  checkUserInput();
  
  // Send message if we have input and address
  if (lora_input != "" && address != "") {
    send_command(lora_input, address);
    
    // Clear after sending unless in repeat mode
    if (!reporting_lock) {
      lora_input = "";
      address = "";
    }
  }
  
  delay(10);
}

/* void loop() {
  checkUserInput();

  // Send message if we have input and address
  if (lora_input != "" && address != "" && !waitingForReply) {
    send_command(lora_input, address);
  }

  // Check for incoming LoRa reply
  if (waitingForReply && lora.available()) {
    String incoming = "";

    unsigned long start = millis();
    while (millis() - start < 1000) {
      if (lora.available()) {
        char c = lora.read();
        if (c == '\n') break;
        incoming += c;
      }
    }

    if (incoming.indexOf("+RCV=") != -1) {
      unsigned long roundTrip = millis() - sendStartTime;

      Serial.println("=== REPLY RECEIVED ===");
      Serial.println(incoming);
      Serial.print("Round-trip time (ms): ");
      Serial.println(roundTrip);
      Serial.println("======================");

      waitingForReply = false;

      if (!reporting_lock) {
        lora_input = "";
        address = "";
      }
    }
  }

  delay(10);
} */

void checkUserInput() {
  if (Serial.available() > 0) {
    String userInput = Serial.readStringUntil('\n');
    userInput.trim();
    
    if (userInput.length() > 0) {
      // Check if the input ends with ":repeat"
      bool isRepeat = userInput.endsWith(":repeat");
      
      if (isRepeat) {
        int colonIndex = userInput.indexOf(':');
        if (colonIndex != -1) {
          userInput = userInput.substring(0, colonIndex);
          reporting_lock = true;
          Serial.println("Repeat mode enabled");
        }
      } else {
        reporting_lock = false;
      }
      
      // Parse command and address
      int commaIndex = userInput.indexOf(",");
      if (commaIndex != -1) {
        lora_input = userInput.substring(0, commaIndex);
        address = userInput.substring(commaIndex + 1);
        
        Serial.print("Command: ");
        Serial.print(lora_input);
        Serial.print(" | Address: ");
        Serial.println(address);
      } else {
        Serial.println("ERROR: Invalid format. Use: <command>,<address>");
      }
    }
  }
}

void send_command(String inputString, String address) {
  int len = inputString.length();
  int addressInt = address.toInt();
  
  if (addressInt < 0 || addressInt > 65535) {
    Serial.println("ERROR: Invalid address");
    return;
  }
  
  Serial.print("Preparing to send: '");
  Serial.print(inputString);
  Serial.print("' (length: ");
  Serial.print(len);
  Serial.print(") to address ");
  Serial.println(addressInt);
  
  // Build the AT command
  // Format: AT+SEND=<address>,<length>,<data>
  char atCommand[100];
  
  snprintf(atCommand, sizeof(atCommand), "AT+SEND=%d,%d,%s", 
           addressInt, len, inputString.c_str());
  
  Serial.print("AT Command: ");
  Serial.println(atCommand);
  
  sendStartTime = millis();
  waitingForReply = true;
  String response = sendATcommand(atCommand, 2000);
  
  // Check if send was successful
  if (response.indexOf("OK") != -1) {
    Serial.println("✓ Send successful");

  } else if (response.indexOf("+ERR") != -1) {
    Serial.println("✗ Send failed - Error response");
    if (!reporting_lock) {  
      lora_input = "";
      address = "";
    }
  } else {
    Serial.println("? Unknown response");
  }
  
  delay(100);  // Small delay between sends
}

/* String sendATcommand(const char *toSend, unsigned long milliseconds) {
  String result = "";
  
  Serial.print("Sending: ");
  Serial.println(toSend);
  
  // Clear any pending data in buffer
  while (lora.available()) {
    lora.read();
  }
  
  lora.println(toSend);
  
  unsigned long startTime = millis();
  Serial.print("Received: ");
  
  while (millis() - startTime < milliseconds) {
    if (lora.available()) {
      char c = lora.read();
      Serial.write(c);
      result += c;
    }
  } 
  
  Serial.println();
  return result;
} */

String sendATcommand(const char *toSend, unsigned long milliseconds) {
  String result = "";
  String line = "";

  Serial.print("Sending: ");
  Serial.println(toSend);

  while (lora.available()) lora.read();  // clear buffer

  lora.println(toSend);

  unsigned long startTime = millis();
  Serial.print("Received: ");

  while (millis() - startTime < milliseconds) {
    while (lora.available()) {
      char c = lora.read();
      Serial.write(c);
      result += c;

      if (c == '\n') {
        // FULL LINE RECEIVED → check for ACK
        if (line.indexOf("+RCV=") != -1 && line.indexOf("OK") != -1 && waitingForReply) {
          unsigned long rtt = millis() - sendStartTime;

          Serial.println("\n=== ACK RECEIVED ===");
          Serial.println(line);
          Serial.print("Round-trip time (ms): ");
          Serial.println(rtt);
          Serial.println("====================");

          waitingForReply = false;
        }
        line = "";
      } else if (c != '\r') {
        line += c;
      }
    }
  }

  Serial.println();
  return result;
}