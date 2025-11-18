#include <stdio.h>
#include <stdlib.h>
#include <wiringPi.h>

using namespace std;

#include <iostream>
#include <fstream>

#include <time.h>

// Makefile needed
// -lwiringPi

// Global counters
static volatile int counters[] = {0, 0, 0, 0, 0, 0, 0};

// Prototypes
void interrupt0(void);
void interrupt1(void);
void interrupt2(void);
void interrupt3(void);
void interrupt4(void);
// void interrupt5 (void);
// void interrupt6 (void);

// Argv 1 byte to output.
int main(int argc, char** argv) {
    if (argc < 2) {
        cout << "You dummy! need to provide an output filename" << endl;
        exit(1);
    }

    time_t rawtime;
    struct tm* timeinfo;

    ofstream output;

    wiringPiSetup();

    // Enable interrupts
    wiringPiISR(02, INT_EDGE_RISING, &interrupt0); // GPIO27  => CH0 && CH1
    wiringPiISR(01, INT_EDGE_RISING, &interrupt1); // GPIO18  => CH0 && CH2
    wiringPiISR(00, INT_EDGE_RISING, &interrupt2); // GPIO17  => CH1 && CH2
    wiringPiISR(21, INT_EDGE_RISING, &interrupt3); // GPIO6  => CH3 (Assuming GPIO22 for CH3)
    wiringPiISR(22, INT_EDGE_RISING, &interrupt4); // GPIO5  => CH3 (Assuming GPIO23 for CH3)

    while (1) {
        delay(60000);
        // delay(6000);
        output.open(argv[1], std::ofstream::out | std::ofstream::app);
        time(&rawtime);
        timeinfo = localtime(&rawtime);
        printf("%d,%d,%d,%d,%d,%s",counters[4], counters[3], counters[2], counters[1], counters[0], asctime(timeinfo));
        output << counters[4] << ", "<< counters[3] << ", " << counters[2] << ", " << counters[1] << ", " << counters[0] << ", " << asctime(timeinfo);
        for (int i = 0; i < sizeof(counters) / sizeof(counters[0]); i++) {
            counters[i] = 0;
        }
        output.close();
    }

    return 0;
}

void interrupt0(void) {
    counters[0]++;
}

void interrupt1(void) {
    counters[1]++;
}

void interrupt2(void) {
    counters[2]++;
}

void interrupt3(void) {
    counters[3]++;
}

 void interrupt4 (void){
   counters[4]++ ;
 }

// void interrupt5 (void){
//   counters[5]++ ;
// }

// void interrupt6 (void){
//   counters[6]++ ;
// }
