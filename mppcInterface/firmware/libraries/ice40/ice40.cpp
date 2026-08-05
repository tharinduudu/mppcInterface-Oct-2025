// ice40.cpp — iCE40 (LP384) SPI flasher, keeps Pi GPCLK @ 9.6 MHz
// - SPI MODE 0
// - Reads full .bin (no hard-coded size)
// - Streams in chunks
// Build: g++ -O2 -std=c++11 -Wall -lwiringPi -lwiringPiDev -c ice40.cpp

#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <vector>
#include <fstream>
#include <algorithm>

#include <wiringPi.h>
#include <wiringPiSPI.h>

#include "ice40.h"

ICE40::ICE40(const uint8_t CS_PIN, const uint8_t DONE_PIN, const uint8_t RST_PIN, const uint8_t SPI_CHANNEL) {
  _CS_PIN      = CS_PIN;
  _DONE_PIN    = DONE_PIN;
  _RST_PIN     = RST_PIN;
  _SPI_CHANNEL = SPI_CHANNEL;
  // SPI @ 4 MHz (safe)
  setup(_SPI_CHANNEL, 4000000);
}

void ICE40::setup(const uint8_t SPI_CHANNEL, const uint32_t clkSpeed) {
  wiringPiSetup();

  // Keep your original GPCLK: WiringPi pin 7 (BCM4) @ 9.6 MHz
  pinMode(7, GPIO_CLOCK);
  gpioClockSet(7, 9600000);

  // iCE40 expects SPI mode 0
  if (wiringPiSPISetupMode(SPI_CHANNEL, clkSpeed, 0) < 0) {
    std::perror("wiringPiSPISetupMode");
  }

  pinMode(_CS_PIN,   OUTPUT);
  pinMode(_RST_PIN,  OUTPUT);
  pinMode(_DONE_PIN, INPUT);
  pullUpDnControl(_DONE_PIN, PUD_UP);

  digitalWrite(_CS_PIN,  HIGH);
  digitalWrite(_RST_PIN, HIGH);
}

void ICE40::configure(const char filename[]) {
  writeFile(filename);
}

void ICE40::writeFile(const char filename[]) {
  std::ifstream f(filename, std::ios::binary);
  if (!f) {
    std::perror("open bitstream");
    return;
  }

  // Read entire file
  std::vector<unsigned char> data((std::istreambuf_iterator<char>(f)),
                                  std::istreambuf_iterator<char>());
  f.close();

  if (data.empty()) {
    std::fprintf(stderr, "ERROR: Empty bitstream: %s\n", filename);
    return;
  }

  std::size_t bitstreamSize = data.size();
  std::printf("Bitstream size: 0x%zx (%zu bytes)\n", bitstreamSize, bitstreamSize);

  // Header expects uint16_t; LP384 bitstreams fit comfortably
  burnData(data.data(), static_cast<uint16_t>(bitstreamSize));
}

void ICE40::burnData(unsigned char* data, uint16_t length) {
  clear();

  // 8 dummy clocks with CS high
  unsigned char dmy[8] = {0};
  wiringPiSPIDataRW(_SPI_CHANNEL, dmy, sizeof(dmy));

  // Hold our dedicated CS low during streaming
  digitalWrite(_CS_PIN, LOW);

  // Stream the bitstream in chunks
  const int CHUNK = 4096;
  uint16_t sent = 0;
  while (sent < length) {
    int n = std::min<int>(CHUNK, static_cast<int>(length - sent));
    if (wiringPiSPIDataRW(_SPI_CHANNEL, data + sent, n) < 0) {
      std::perror("wiringPiSPIDataRW");
      break;
    }
    sent += static_cast<uint16_t>(n);
  }

  // Deassert CS
  digitalWrite(_CS_PIN, HIGH);

  // Extra clocks to flush
  unsigned char tail[16] = {0};
  wiringPiSPIDataRW(_SPI_CHANNEL, tail, sizeof(tail));

  // Wait for DONE to go high (up to ~1 s)
  unsigned int guard_ms = 1000;
  while (!digitalRead(_DONE_PIN) && guard_ms--) delay(1);

  if (!digitalRead(_DONE_PIN)) {
    std::fprintf(stderr, "ERROR: DONE pin did not go high. Configuration may have failed.\n");
  } else {
    std::printf("DONE=1 (configuration successful)\n");
  }
}

void ICE40::clear() {
  digitalWrite(_CS_PIN,  LOW);
  digitalWrite(_RST_PIN, LOW);
  delayMicroseconds(200);
  digitalWrite(_RST_PIN, HIGH);
  delayMicroseconds(1200);
  digitalWrite(_CS_PIN,  HIGH);
}
