from flask import Flask
from flask import jsonify
from threading import Thread
from threading import Lock
import time
import os.path
import json
import serial
import crcmod

# ---- Global vars ----

ser = None  # Переменная для взамодействия с COM портом.
port = 'COM10'  # Порт для связи с Arduino.
position = 0.0  # Позиция экрана от 0 до 100.
position_lock = Lock()  # Мьютекс для переменной position.
MAX_POSITION = 2680  # Максимальное значение позиции экрана, приходящее с Arduino.
bytes_buff = b''
bytes_buff_index = 0

# ---- HDLC Part ----


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
    global bytes_buff_index

    frame = b''  # Фрейм, состоящий из байтов.
    is_escape_char = False

    while True:
        if bytes_buff_index + 1 >= len(bytes_buff):
            return 'not_finished', 0

        current_byte = bytes_buff[bytes_buff_index:bytes_buff_index + 1]
        bytes_buff_index += 1

        # Проверка для предотвращения зацикленности.
        if len(frame) > 32:
            print('Frame length error.')
            return 'bad_frame', 0

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

                # Если CRC не совпадают, то значит пришел ошибочный фрейм.
                if frame_crc != check_crc:
                    print('CRC error. Frame CRC: {0}. Check CRC: {1}. Full Frame {2}'.format(str(frame_crc), check_crc, str(frame)))
                    return 'bad_frame', 0

                # Отдаем значение приведенное к процентам, с двумя знаками после запятой.
                num = int(data[:len(data)-1])
                res = round((num * 100 / MAX_POSITION), 2)
                return 'ok', res


# ---- Backend (Flask) Part ----


app = Flask(__name__)
app.debug = True


@app.route('/screen', methods=["GET"])
def screen():
    position_lock.acquire()
    data = {'name': 'Display position', 'position': position}
    position_lock.release()
    response = jsonify(data)

    # Включить Access-Control-Allow-Origin
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response


def position_updater():
    global ser
    global position
    global bytes_buff
    global bytes_buff_index

    prev_frame_part = b''
    while True:
        try:
            if ser is None:
                raise serial.SerialException
            bytes_buff = ser.read(1)
            bytes_buff += ser.read(ser.in_waiting)

            # i = max(1, min(2048, ser.in_waiting))
            # bytes_buff = ser.read(i)

            print(bytes_buff)
            if len(bytes_buff) == 0:
                time.sleep(0.0001)
                continue

            bytes_buff = prev_frame_part + bytes_buff
            bytes_buff_index = 0
            prev_frame_part = b''

            while bytes_buff_index < len(bytes_buff):
                try:
                    current_frame_index = bytes_buff_index

                    status, local_position = read_frame()  # Считываем значение кадра
                    if status == 'ok':
                        # Критическая секция
                        position_lock.acquire()
                        position = local_position
                        position_lock.release()
                        print(position)
                    elif status == 'not_finished':
                        #print('Not finished')
                        prev_frame_part = bytes_buff[bytes_buff_index:len(bytes_buff)]
                        break

                except Exception as e:
                    print(e)



        except serial.SerialException:
            try:
                if ser is None:
                    ser = serial.Serial(port, 9600, timeout=0)
                    ser.flush()
                    continue
                ser.close()
                ser = serial.Serial(port, 9600, timeout=0)
                ser.flush()
            except Exception as e:
                time.sleep(3)
                print(e)
                continue
        except CRCError as e:
            print(e)
        except FrameLengthError as e:
            print(e)
        except Exception as e:
            print(e)
        #time.sleep(0.001)


def read_config():
    global port

    file = None
    file_name = 'config.json'
    file_exists = os.path.isfile(file_name)
    if file_exists:
        file = open(file_name, 'r')
        data = file.read()
        port = json.loads(data)['port']

    else:
        file = open(file_name, 'w+')
        data = json.dumps({'port': port})
        file.write(data)
    file.close()


if __name__ == "__main__":
    try:
        read_config()
    except Exception as e:
        print(e)

    # Поток, который обновляет позицию экрана
    positionThread = Thread(target=position_updater, args=[])
    positionThread.start()

    print('Arduino reader thread started.')
    print('Screen position:', 'http://127.0.0.1:5000/screen')

    # Flask
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
