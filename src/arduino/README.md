About arduino:
arduino hosts '.ino' files created and updated primarily in the Arduino IDE. This folder holds the src for the GSC ESP. We eventually plan to migrate to PlatformIO (either IDE or VS Code extension) for better library/firmware support and header/library/src organization.

'GSC_ESP.ino'
    Initializes GSC TX/RX LoRa's. Transmits commands from Jetson to TS ESP and data from Teensy to Jetson.
'TS_ESP.ino'
    Initializes TS RX LoRa. Transmits valid commands to relays.
'TS_TEENSY-single.ino'
    A simplified, "single-threaded" codeset that takes incoming debug from TS ESP (refer to './TS_ESP.ino') and sends back to Jetson.