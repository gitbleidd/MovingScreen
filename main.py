from flask import Flask
from flask import jsonify
from threading import Thread
from threading import Lock
import time
import os.path
import json
import serial
import crcmod
import serial.tools.list_ports

# ---- Global vars ----
ser = None  # Переменная для взамодействия с COM портом.
position_lock = Lock()  # Мьютекс для переменной position.
position = 0.0  # Позиция экрана от 0 до 100.

# Порт для связи с Arduino.
# Атрибут по которому будет искаться порт.
# Максимальное значение позиции экрана, приходящее с Arduino.
# Минимальное значение позиции экрана, приходящее с Arduino.
config = {'port': 'COM10',
          'portSearchAttribute': 'Arduino',
          'minPosition': 0,
          'maxPosition': 2680}

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
    frame = b''  # Фрейм, состоящий из байтов.
    is_escape_char = False

    while True:
        current_byte = ser.read(1)

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
                min_pos = config['minPosition']
                max_pos = config['maxPosition']
                num = int(data[:len(data)-1])
                res = round((num * 100 / max_pos), 2)
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
    port = config['port']
    while True:
        try:
            if ser is None:
                raise serial.SerialException

            status, local_position = read_frame()  # Считываем значение кадра

            if status == 'ok':
                # Критическая секция
                position_lock.acquire()
                position = local_position
                position_lock.release()
                print(position)

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


def read_config() -> dict:
    file_name = 'config.json'
    file_exists = os.path.isfile(file_name)

    if file_exists:
        with open(file_name, 'r') as f:
            derived_config = json.load(f)
        return derived_config
    else:
        # Записываем стандартный конфиг
        with open(file_name, 'w+') as f:
            json.dump(config, f)
        return config


def setup_config(derived_config: dict):
    """
    Копирует значения из полученного конфига.
    Ищет порт, если установлен новый порт или указан порт по умолчанию.
    """

    global config
    searched_port = search_port(config['portSearchAttribute'])

    # Если в поле порта ничего нет,
    # или там указан порт порт по умолчанию,
    # то устанавливаем только что найденный порт.
    if searched_port is not None and (derived_config['port'] == config['port'] or derived_config['port'] == ''):
        config['port'] = searched_port
    else:
        config['port'] = derived_config['portSearchAttribute']

    # Копируем оставшиеся значения.
    config['portSearchAttribute'] = derived_config['portSearchAttribute']
    config['minPosition'] = derived_config['minPosition']
    config['maxPosition'] = derived_config['maxPosition']


def search_port(search_param='Arduino'):
    """
    Возвращает порт на основе введенного параметра,
    либо None в случае неудачи.
    """
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        if search_param in p.description:
            return p.device
    return None



if __name__ == "__main__":
    # TODO распознование порта для arudino mega -
    #  просто вывести ключевые слова в конфиг и по ним ищем.

    # TODO добавить минимальное значение экрана с arduino;
    #  на основе дельты delta = (max-min) считатать все.

    # TODO поробовать добавить сокеты.

    read_status, derived_config = False, config
    try:
        derived_config = read_config()
        setup_config(derived_config)
    except json.decoder.JSONDecodeError as e:
        print(e)
    except Exception as e:
        print(e)
        print('Config file corrupted or something went wrong. '
              'Default settings will be loaded.')
    finally:
        print('Current config: ', config)


    # Поток, который обновляет позицию экрана
    positionThread = Thread(target=position_updater, args=[])
    positionThread.start()

    print('Arduino reader thread started.')
    print('Screen position:', 'http://127.0.0.1:5000/screen')

    # Flask
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
