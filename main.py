from flask import Flask
from flask import jsonify
from datetime import datetime
from threading import Thread
from threading import Lock
import time

position_lock = Lock()

# ---- Global vars ----

ser = None  # Переменная для взамодействия с COM портом.
position = 0  # Позиция экрана от 0 до n.
f = open("hdlc_output.txt", "a")

# ---- HDLC Part ----

import serial
import crcmod


class CRCError(Exception):
    def __init__(self, text):
        self.txt = text


class FrameLengthError(Exception):
    def __init__(self, text):
        self.txt = text


# Функция, принимающая байты и возращающая строку CRC суммы.
def hdlc_crc(data):
    crc16 = crcmod.predefined.Crc('crc-16-mcrf4xx')
    crc16.update(data)
    crc = crc16.hexdigest()
    return crc


def read_frame():
    frame = b''  # Фрейм, состоящий из байтов.
    is_escape_char = False

    while True:
        current_byte = ser.read(1)

        # Debug:
        #print(current_byte.hex(), end=' ')

        # Проверка для предотвращения зацикленности.
        if len(frame) > 32:
            raise FrameLengthError('Frame length error.')

        # Если пришло начало фрейма '~', то пробуем считать фрейм дальше.
        if current_byte == b'~' and len(frame) == 0:
            frame += current_byte
            continue

        # Если попался символ экранирования , то запоминаем.
        if current_byte == b'\x7D' and len(frame) > 0:
            is_escape_char = True
            continue

        if len(frame) > 0:

            # Если предыдущий символ был символом экранизации, то надо преобразовать текущий.
            # Приводим текущий байт к int, делаем xor 0x20 и приводим обратно к bytes.
            # Пропускаем текущую итерацию, чтобы не посчтитало за конец фрейма.
            if is_escape_char:
                current_byte = (ord(current_byte) ^ 0x20).to_bytes(1, byteorder='big')
                is_escape_char = False
                frame += current_byte
                continue

            if current_byte == b'~' and len(frame) == 1:
                continue

            frame += current_byte

            if current_byte == b'~':  # Если дочитали до конца фрейма, то возращаем массив байтов.

                data = frame[1:len(frame) - 3]  # Данные из из фрейма.
                frame_crc = (frame[-2:-4:-1]).hex().lower()  # CRC16 из фрейма.
                check_crc = hdlc_crc(data).lower()  # Пересчитанная CRC16.

                # Debug part:

                #print(frame, time.time())
                #print("---", time.time(), end="")
                #print()

                #print(data[:len(data)-1].decode())

                # --- --- ---

                # Если CRC не совпадают, то значит пришел ошибочный фрейм.
                if frame_crc != check_crc:
                    raise CRCError('CRC error. Frame CRC: {0}. Check CRC: {1}. Full Frame {2}'.format(str(frame_crc), check_crc, str(frame)))

                f.write(str(data[:len(data) - 1].decode()))
                f.write("\n")
                return data[:len(data)-1].decode()


# ---- Backend (Flask) Part ----


app = Flask(__name__)
app.debug = True


@app.route('/')
def index():
    return 'Index Page'


@app.route("/main")
def test():
    position_lock.acquire()
    global position
    data = {'name': 'Display position', 'position': position}
    position_lock.release()
    return jsonify(data)


def position_updater():
    global ser
    global position

    local_position = 0
    while True:
        try:
            if ser is None:
                raise serial.SerialException
            local_position = int(read_frame())
        except serial.SerialException:
            try:
                if ser is None:
                    ser = serial.Serial('COM10', 9600, timeout=0)
                    ser.flush()
                    continue
                ser.close()
                ser = serial.Serial('COM10', 9600, timeout=0)
                ser.flush()
            except Exception as e:
                print(e)
                continue
        except CRCError as e:
            print(e)
        except FrameLengthError as e:
            print(e)
        except Exception as e:
            print(e)

        position_lock.acquire()
        position = local_position
        position_lock.release()
        #time.sleep(0.001)


if __name__ == "__main__":
    # Поток, который обновляет глобаль
    positionThread = Thread(target=position_updater, args=[])
    positionThread.start()

    app.run(host='0.0.0.0', debug=False, threaded=True)
