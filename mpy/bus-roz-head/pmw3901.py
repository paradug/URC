import pyb
import struct
from machine import SPI

WAIT = -1

SPI_CS = pyb.Pin('C5', pyb.Pin.OUT_PP)
SPI_BUS = 2

REG_ID = 0x00
REG_DATA_READY = 0x02
REG_MOTION_BURST = 0x16
REG_POWER_UP_RESET = 0x3a
REG_ORIENTATION = 0x5b


class PMW3901():
  def __init__(self, spi_port=SPI_BUS, spi_cs_gpio=SPI_CS):
    self.spi_cs_pin = spi_cs_gpio
    self.spi_dev = SPI(spi_port)

    self.spi_cs_pin.value(0)
    pyb.delay(50)
    self.spi_cs_pin.value(1)

    self._write(REG_POWER_UP_RESET, 0x5a)
    pyb.delay(20)
    for offset in range(5):
      self._read(REG_DATA_READY + offset)

    self._secret_sauce()

    product_id, revision = self.get_id()
    if product_id != 0x49 or revision != 0x00:
      raise RuntimeError("Invalid Product ID or Revision for PMW3901: 0x{:02x}/0x{:02x}".format(product_id, revision))
    # print("Product ID: {}".format(ID.get_product_id()))
    # print("Revision: {}".format(ID.get_revision_id()))

  def get_id(self):
    """Get chip ID and revision from PMW3901."""
    return self._read(REG_ID, 2)

  def set_rotation(self, degrees=0):
    """Set orientation of PMW3901 in increments of 90 degrees.
    :param degrees: rotation in multiple of 90 degrees
    """
    if degrees == 0:
      self.set_orientation(invert_x=True, invert_y=True, swap_xy=True)
    elif degrees == 90:
      self.set_orientation(invert_x=False, invert_y=True, swap_xy=False)
    elif degrees == 180:
      self.set_orientation(invert_x=False, invert_y=False, swap_xy=True)
    elif degrees == 270:
      self.set_orientation(invert_x=True, invert_y=False, swap_xy=False)
    else:
      raise TypeError("Degrees must be one of 0, 90, 180 or 270")

  def set_orientation(self, invert_x=True, invert_y=True, swap_xy=True):
    """Set orientation of PMW3901 manually.
    Swapping is performed before flipping.
    :param invert_x: invert the X axis
    :param invert_y: invert the Y axis
    :param swap_xy: swap the X/Y axes
    """
    value = 0
    if swap_xy:
      value |= 0b10000000
    if invert_y:
      value |= 0b01000000
    if invert_x:
      value |= 0b00100000
    self._write(REG_ORIENTATION, value)

  def get_motion(self, timeout=5000):
    """Get motion data from PMW3901 using burst read.
    Reads 12 bytes sequentially from the PMW3901 and validates
    motion data against the SQUAL and Shutter_Upper values.
    Returns Delta X and Delta Y indicating 2d flow direction
    and magnitude.
    :param timeout: Timeout in seconds
    """
    t_start = pyb.millis()
    while pyb.elapsed_millis(t_start) < timeout:
      self.spi_cs_pin.value(0)
      data = bytearray(13)
      self.spi_dev.write_readinto(bytearray([REG_MOTION_BURST] + [0 for x in range(12)]), data)
      self.spi_cs_pin.value(1)
      (_, dr, obs,
       x, y, quality,
       raw_sum, raw_max, raw_min,
       shutter_upper,
       shutter_lower) = struct.unpack("<BBBhhBBBBBB", bytearray(data))

      if quality > 0x19:
        return x, y

      pyb.delay(10)

    raise RuntimeError("Timed out waiting for motion data.")

  def get_motion_slow(self, timeout=5000):
    """Get motion data from PMW3901.
    Returns Delta X and Delta Y indicating 2d flow direction
    and magnitude.
    :param timeout: Timeout in seconds
    """
    t_start = pyb.millis()
    while pyb.elapsed_millis(t_start) < timeout:
      data = self._read(REG_DATA_READY, 5)
      dr, x, y = struct.unpack("<Bhh", bytearray(data))
      if dr & 0b10000000:
        return x, y
      pyb.delay(1)

    raise RuntimeError("Timed out waiting for motion data.")

  def _write(self, register, value):
    self.spi_cs_pin.value(0)
    self.spi_dev.write(bytearray([register | 0x80, value]))
    self.spi_cs_pin.value(1)

  def _read(self, register, length=1):
    result = []
    for x in range(length):
      self.spi_cs_pin.value(0)
      value = bytearray(2)
      self.spi_dev.write_readinto(bytearray([register + x, 0]), value)
      self.spi_cs_pin.value(1)
      result.append(value[1])

    if length == 1:
      return result[0]
    else:
      return result

  def _bulk_write(self, data):
    for x in range(0, len(data), 2):
      register, value = data[x:x + 2]
      if register == WAIT:
        # print("Sleeping for: {:02d}ms".format(value))
        pyb.delay(value)
      else:
        # print("Writing: {:02x} to {:02x}".format(register, value))
        self._write(register, value)

  def _secret_sauce(self):
    """Write the secret sauce registers.
    Don't ask what these do, the datasheet refuses to explain.
    They are some proprietary calibration magic.
    """
    self._bulk_write([
      0x7f, 0x00,
      0x55, 0x01,
      0x50, 0x07,

      0x7f, 0x0e,
      0x43, 0x10
    ])
    if self._read(0x67) & 0b10000000:
      self._write(0x48, 0x04)
    else:
      self._write(0x48, 0x02)
    self._bulk_write([
      0x7f, 0x00,
      0x51, 0x7b,

      0x50, 0x00,
      0x55, 0x00,
      0x7f, 0x0E
    ])
    if self._read(0x73) == 0x00:
      c1 = self._read(0x70)
      c2 = self._read(0x71)
      if c1 <= 28:
        c1 += 14
      if c1 > 28:
        c1 += 11
      c1 = max(0, min(0x3F, c1))
      c2 = (c2 * 45) // 100
      self._bulk_write([
        0x7f, 0x00,
        0x61, 0xad,
        0x51, 0x70,
        0x7f, 0x0e
      ])
      self._write(0x70, c1)
      self._write(0x71, c2)
    self._bulk_write([
      0x7f, 0x00,
      0x61, 0xad,
      0x7f, 0x03,
      0x40, 0x00,
      0x7f, 0x05,

      0x41, 0xb3,
      0x43, 0xf1,
      0x45, 0x14,
      0x5b, 0x32,
      0x5f, 0x34,
      0x7b, 0x08,
      0x7f, 0x06,
      0x44, 0x1b,
      0x40, 0xbf,
      0x4e, 0x3f,
      0x7f, 0x08,
      0x65, 0x20,
      0x6a, 0x18,

      0x7f, 0x09,
      0x4f, 0xaf,
      0x5f, 0x40,
      0x48, 0x80,
      0x49, 0x80,

      0x57, 0x77,
      0x60, 0x78,
      0x61, 0x78,
      0x62, 0x08,
      0x63, 0x50,
      0x7f, 0x0a,
      0x45, 0x60,
      0x7f, 0x00,
      0x4d, 0x11,

      0x55, 0x80,
      0x74, 0x21,
      0x75, 0x1f,
      0x4a, 0x78,
      0x4b, 0x78,

      0x44, 0x08,
      0x45, 0x50,
      0x64, 0xff,
      0x65, 0x1f,
      0x7f, 0x14,
      0x65, 0x67,
      0x66, 0x08,
      0x63, 0x70,
      0x7f, 0x15,
      0x48, 0x48,
      0x7f, 0x07,
      0x41, 0x0d,
      0x43, 0x14,

      0x4b, 0x0e,
      0x45, 0x0f,
      0x44, 0x42,
      0x4c, 0x80,
      0x7f, 0x10,

      0x5b, 0x02,
      0x7f, 0x07,
      0x40, 0x41,
      0x70, 0x00,
      WAIT, 0x0A,  # Sleep for 10ms

      0x32, 0x44,
      0x7f, 0x07,
      0x40, 0x40,
      0x7f, 0x06,
      0x62, 0xf0,
      0x63, 0x00,
      0x7f, 0x0d,
      0x48, 0xc0,
      0x6f, 0xd5,
      0x7f, 0x00,

      0x5b, 0xa0,
      0x4e, 0xa8,
      0x5a, 0x50,
      0x40, 0x80,
      WAIT, 0xF0,

      0x7f, 0x14,  # Enable LED_N pulsing
      0x6f, 0x1c,
      0x7f, 0x00
    ])


if __name__ == "__main__":
  import argparse
  parser = argparse.ArgumentParser()
  parser.add_argument('--rotation', type=int,
            default=0, choices=[0, 90, 180, 270],
            help='Rotation of sensor in degrees.', )
  args = parser.parse_args()
  flo = PMW3901(spi_port=2, spi_cs_gpio=SPI_CS)
  flo.set_rotation(args.rotation)
  tx = 0
  ty = 0
  try:
    while True:
      try:
        x, y = flo.get_motion()
      except RuntimeError:
        continue
      tx += x
      ty += y
      print("Motion: {:03d} {:03d} x: {:03d} y {:03d}".format(x, y, tx, ty))
      pyb.delay(10)
  except KeyboardInterrupt:
    pass
