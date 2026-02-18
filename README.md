About Torito-25-26:
As it stands, there are two primary folders: archive and src. archive organizes reference and to-be-implemented code while src organizes current GSC code architecture. A little more on the current structure: we are using a Jetson Nano Original B01 connected to a XIAO_ESP32S3 via USB3.0 Type A (USB if the fastest and simplest protocol we could find to implement for this situation). The GSC ESP does all the TX/RX, hosting two LoRa's on UART1 and UART2.

GSC ESP LoRa TX -> TS ESP RX <-> Relays
                       |
GSC ESP LoRa RX <- TS TEENSY TX

dir 'archive'
    Holds archived files.
dir 'src'
    Holds src files for Jetson Nano & dual-LoRa ESP.
'.gitignore'
    Hides dir and files from git.

Documentation:
https://pbrobinson.fedorapeople.org/SP-09732-001_v1.1.pdf - Jetson B01
https://www.reddit.com/r/UsbCHardware/comments/1hmb79v/ultimate_usb_chart/#lightbox - Ultimate USB
https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/ - XIAO ESP32S3
https://reyax.com/upload/products_download/download_file/LoRa_AT_Command_RYLR998_RYLR498_EN.pdf - LoRa RYLR998
https://www.homemade-circuits.com/communication-protocols-in-microcontrollers-explained-%EF%BF%BC/ - Communication Protocols

Additional Comments:
We use a TP-Link Extender to provide Wi-Fi capability since the Jetson Nano has a 1Gbps ethernet port, but no Wi-Fi chip.
'...-save...' files are temporary duplicates made for immediate testing. PLEASE fork/pull-request or commit/push for anything larger than single file edits. I refuse to do this all over again.