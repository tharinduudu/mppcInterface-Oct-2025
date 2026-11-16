#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>

#include "max1932.h"

namespace {
const char* defaultCalibration = "/etc/glowcost/max1932-calibration.conf";
constexpr unsigned dividerTop = 95400, dividerBottom = 2064, dacResistor = 2317;

int hexByte(const std::string& text) {
  if (text.empty() || text.find_first_not_of("0123456789abcdefABCDEFxX") != std::string::npos)
    throw std::runtime_error("Expected a hex byte (0x00 through 0xFF)");
  char* end;
  long value = std::strtol(text.c_str(), &end, 16);
  if (end == text.c_str() || *end || value < 0 || value > 255)
    throw std::runtime_error("Expected a hex byte (0x00 through 0xFF)");
  return static_cast<int>(value);
}

struct Calibration {
  double intercept;
  double slope;
  int first;
  int last;

  double voltage(int code) const { return intercept + slope * code; }
  bool covers(int code) const { return code >= first && code <= last; }

  int codeFor(double volts) const {
    // Allow half a displayed millivolt for rounding at the endpoints.
    if (volts < voltage(last) - 0.0005 || volts > voltage(first) + 0.0005)
      throw std::runtime_error("Voltage is outside this board's calibrated range");
    if (volts <= voltage(last))
      return last;
    if (volts >= voltage(first))
      return first;
    int code = static_cast<int>(std::lround((volts - intercept) / slope));
    if (!covers(code))
      throw std::runtime_error("No calibrated code for that voltage");
    return code;
  }
};

bool loadCalibration(const char* path, bool explicitPath, Calibration& calibration) {
  errno = 0;
  std::ifstream file(path);
  if (!file) {
    if (!explicitPath && errno == ENOENT)
      return false;
    throw std::runtime_error(std::string("Cannot read calibration: ") + path);
  }
  std::stringstream data;
  std::string line, first, last, extra;
  while (std::getline(file, line))
    data << line.substr(0, line.find('#')) << '\n';
  if (file.bad() || !(data >> calibration.intercept >> calibration.slope >> first >> last) ||
      (data >> extra))
    throw std::runtime_error("Calibration needs: intercept_V slope_V_per_code first_hex last_hex");
  calibration.first = hexByte(first);
  calibration.last = hexByte(last);
  if (!std::isfinite(calibration.intercept) || !std::isfinite(calibration.slope) ||
      calibration.slope >= 0 || calibration.first < 1 || calibration.last <= calibration.first ||
      !std::isfinite(calibration.voltage(calibration.first)) ||
      !std::isfinite(calibration.voltage(calibration.last)) || calibration.voltage(calibration.last) <= 0)
    throw std::runtime_error("Invalid calibration range or slope");
  return true;
}

double parseVoltage(const std::string& text, bool millivolts) {
  char* end;
  errno = 0;
  double value = std::strtod(text.c_str(), &end);
  if (text.empty() || end == text.c_str() || errno || !std::isfinite(value) || value < 0 ||
      (*end && (millivolts || (std::string(end) != "V" && std::string(end) != "v"))))
    throw std::runtime_error("Invalid voltage");
  return millivolts ? value / 1000 : value;
}

void usage(const char* program) {
  std::fprintf(stderr,
      "Usage: %s [--predict] [--calibration FILE] HEX | --voltage V | --mV MV\n", program);
}
}

int main(int argc, char** argv) {
  try {
    bool predict = false;
    bool explicitPath = false;
    const char* path = defaultCalibration;
    int arg = 1;
    // Parse all arguments before constructing the GPIO/SPI driver.
    for (; arg < argc; ++arg) {
      std::string flag = argv[arg];
      if (flag == "--predict" || flag == "--dry-run") {
        predict = true;
      } else if (flag == "--calibration" && arg + 1 < argc) {
        path = argv[++arg];
        explicitPath = true;
      } else if (flag == "--help" || flag == "-h") {
        usage(argv[0]);
        return 0;
      } else {
        break;
      }
    }
    if (arg >= argc)
      throw std::runtime_error("Missing MAX1932 code or voltage");

    std::string value = argv[arg++];
    bool millivolts = value == "--mV" || value == "--mv";
    bool voltageMode = millivolts || value == "--voltage" || value == "-v";
    if (voltageMode && arg < argc)
      value = argv[arg++];
    if (arg != argc)
      throw std::runtime_error("Unexpected extra arguments");
    voltageMode = voltageMode || value.find_first_of(".Vv") != std::string::npos;
    double requested = voltageMode ? parseVoltage(value, millivolts) : 0;
    int code = voltageMode ? 0 : hexByte(value);
    Calibration calibration{};
    // An OFF command must work even if the board's calibration file is broken.
    bool calibrated = (code || requested > 0) && loadCalibration(path, explicitPath, calibration);
    if (requested > 0) {
      if (!calibrated)
        throw std::runtime_error("Voltage control requires a measured board calibration");
      code = calibration.codeFor(requested);
    }

    if (!predict) {
      // HV chip select: BCM13 (header pin 33, WiringPi pin 23); SPI channel 0.
      MAX1932 supply(23, 0, dividerTop, dividerBottom, dacResistor);
      supply.setByte(static_cast<uint8_t>(code));
    }

    if (predict || voltageMode)
      std::printf("MAX1932 code: 0x%02X (%u)\n", static_cast<unsigned>(code), static_cast<unsigned>(code));
    else
      std::printf("%d\n", code);  // Keep the legacy raw-code output.

    if (code == 0)
      std::printf("0 V (off command)\n");
    else if (calibrated && calibration.covers(code))
      std::printf("%.3f V (calibrated estimate, not readback)\n", calibration.voltage(code));
    else if (calibrated)
      std::printf("Voltage unknown (code outside calibrated range)\n");
    else {
      // Original divider model, for reference only.
      unsigned base = (1250 * dividerTop) / dividerBottom;
      unsigned span = (1250 * dividerTop) / dacResistor;
      double nominal = (base + span * ((255 - code) / 255.0)) / 1000.0;
      std::printf("%.3f V (nominal estimate; no board calibration)\n", nominal);
    }
  } catch (const std::exception& error) {
    std::fprintf(stderr, "MAX1932: %s\n", error.what());
    return 1;
  }
  return 0;
}
