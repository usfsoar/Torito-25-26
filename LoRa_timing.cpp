#include <libserial/SerialStream.h>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <unistd.h>

using namespace std;
using namespace LibSerial;

constexpr const char* const SERIAL_PORT = "/dev/ttyTHS1";

SerialStream lora;

int init(){
    try
    {
        // Open the Serial Ports at the desired hardware devices.
        lora.Open(SERIAL_PORT) ;
    }
    catch (const OpenFailed&)
    {
        cerr << "The serial ports did not open correctly." << endl ;
        return EXIT_FAILURE ;
    }

    lora.setBaudRate(BaudRate::BAUD_115200);
    return 0;

}
void lora_int(){
    lora.Write("AT\r\n");
    string response;
    lora.read (response, 0);
    if(response.indexof("OK") != -1){
        cout<<"LoRa module responding"<<endl;
    }else{
        cout<<"LoRa module not responding!"<<endl;
    }

    lora.Write("AT+ADDRESS=2\r\n");
    lora.Write("AT+BAND=915000000\r\n");
    lora.Write("AT+NETWORKID=5\r\n");
    while (lora.IsDataAvailable()){
        lora.read(response, 0);
    }

}
int main(){
    init();
    lora.init();
    return 0;
}