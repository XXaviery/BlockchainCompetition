#ifndef MOF_HOST_ARDUINO_H
#define MOF_HOST_ARDUINO_H

#include <cmath>
#include <cstdint>
#include <cstdlib>

using std::abs;

template <typename T>
T constrain(T value, T low, T high) {
    return value < low ? low : (value > high ? high : value);
}

template <typename T>
T max(T left, T right) {
    return left > right ? left : right;
}

uint32_t millis(void);

#endif
