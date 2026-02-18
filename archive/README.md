About archive:
archive holds archived dir and files either made obsolete or with a plan for future use. Contents are kept for reference or implementation.

[gui] GUI implementation
'plottingShit.py'
    A demo of static draw ability using matplotlib.
'gscPlots.py'
    A demo of 'gsc_dashboard.py' that constantly draws using matplotlib.

[src_a] C++ implementation
    Contains src for C++ on Jetson Nano for TX and Test Stand ESP32 for RX. Will be implemented post Cold Flow 1. Code for Test Stand TX is w/ DAQ. Jetson Nano RX needs to be implemented to meet post CF1 design strategy and Jetson Nano TX may need to be changed.
dir 'GSC_to_Relay_Code'
    Contains 'main_rx.cpp' (TS ESP32 RX) and 'main_tx.cpp' (Jetson Nano TX).
dir 'LoRa_Library'
    Contains header config and provides basic LoRa functionality ('LoRaModule.cpp').

[src_b] For LoRa testing
'lora_receiver.ino'
    Receives tranmission, prints RSSI/SNR/etc., and sends feedback to 'lora_sender.ino'.
'lora_sender.ino'
    Sends the transmission, and receives/prints TIME and other feedback from 'lora_receiver.ino'.