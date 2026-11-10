#pragma once
#include <stdint.h>

// First byte 0x01..0x03 is reserved for the existing servo receiver.
constexpr uint8_t kIrList = 0x40;
constexpr uint8_t kIrLearn = 0x41;
constexpr uint8_t kIrSend = 0x42;
constexpr uint8_t kIrDelete = 0x43;
constexpr uint8_t kIrRename = 0x44;
constexpr uint8_t kIrState = 0x50;
constexpr uint8_t kIrSlots = 32;
constexpr uint8_t kIrNameSize = 49;
struct IrStatePacket {
  uint8_t type, slot, state;
  char name[kIrNameSize];
};
// state: 0 empty, 1 saved, 2 learning, 3 saved after learning,
// 4 send done, 5 timeout, 6 error.
