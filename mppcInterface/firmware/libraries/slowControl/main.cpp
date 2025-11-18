#include <stdio.h>
#include <stdlib.h>
#include <wiringPi.h>
#include <iostream>
#include <fstream>
#include <time.h>

using namespace std;

static volatile int counters[7] = {0};

// Prototypes
void interrupt0(void); // CH0 && CH1
void interrupt1(void); // CH0 && CH2
void interrupt2(void); // CH1 && CH2
void interrupt3(void); // CH0 && CH1 && CH2
void interrupt4(void); // CH0 raw
void interrupt5(void); // CH1 raw
void interrupt6(void); // CH2 raw

int main(int argc, char** argv) {
    if (argc < 2) {
        cout << "Usage: " << argv[0] << " <output_filename>" << endl;
        return 1;
    }

    time_t rawtime;
    struct tm* timeinfo;
    ofstream output;

    wiringPiSetup();

    // Setup interrupts
    wiringPiISR(2,  INT_EDGE_RISING, &interrupt0); // GPIO27
    wiringPiISR(1,  INT_EDGE_RISING, &interrupt1); // GPIO18
    wiringPiISR(0,  INT_EDGE_RISING, &interrupt2); // GPIO17
    wiringPiISR(6,  INT_EDGE_RISING, &interrupt3); // GPIO25
    wiringPiISR(22, INT_EDGE_RISING, &interrupt4); // GPIO6
    wiringPiISR(21, INT_EDGE_RISING, &interrupt5); // GPIO5
    wiringPiISR(27, INT_EDGE_RISING, &interrupt6); // GPIO16

    while (1) {
        delay(60000); // 60 seconds

        time(&rawtime);
        timeinfo = localtime(&rawtime);

        output.open(argv[1], std::ofstream::out | std::ofstream::app);
        output << counters[0] << ", "  // CH0 && CH1
               << counters[1] << ", "  // CH0 && CH2
               << counters[2] << ", "  // CH1 && CH2
               << counters[3] << ", "  // CH0 && CH1 && CH2
               << counters[4] << ", "  // CH0 raw
               << counters[5] << ", "  // CH1 raw
               << counters[6] << ", "  // CH2 raw
               << asctime(timeinfo);

        printf("%d, %d, %d, %d, %d, %d, %d, %s",
               counters[0], counters[1], counters[2],
               counters[3], counters[4], counters[5],
               counters[6], asctime(timeinfo));

        // Reset counters
        for (int i = 0; i < 7; i++) counters[i] = 0;
        output.close();
    }

    return 0;
}

// Interrupt handlers
void interrupt0(void) { counters[0]++; } // CH0 && CH1
void interrupt1(void) { counters[1]++; } // CH0 && CH2
void interrupt2(void) { counters[2]++; } // CH1 && CH2
void interrupt3(void) { counters[3]++; } // CH0 && CH1 && CH2
void interrupt4(void) { counters[4]++; } // CH0 raw
void interrupt5(void) { counters[5]++; } // CH1 raw
void interrupt6(void) { counters[6]++; } // CH2 raw
