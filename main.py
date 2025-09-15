import json
import psycopg2
import os
import time
import sys

with open("config_os.json", "r", encoding="utf-8") as f:
    config = json.load(f)

user = os.getenv("MY_DB_USER")
password = os.getenv("HIS_DB_PASSWORD")

if not user or not password:
    print("Ошибка: переменные окружения MY_DB_USER и HIS_DB_PASSWORD должны быть установлены.")
    exit(1)

interval_min = os.getenv("PING_POSTGRE_DB", "5")
try:
    interval = int(interval_min) * 60
except ValueError:
    print("Ошибка: переменная PING_POSTGRE_DB должна быть числом. Используется автоматически 5 минут.")
    interval = 5 * 60

connect_timeout = int(os.getenv("CONNECT_TIMEOUT_DB", "10"))

log_file_path = os.getenv("LOG_FILE_PATH")
log_file = None
if log_file_path:
    try:
        log_file = open(log_file_path, "a", encoding="utf-8")
    except Exception as e:
        print(f"Не удалось открыть файл для логов: {e}", file=sys.stderr)
        log_file = None

def log_stdout(message):
    print(message)
    if log_file:
        log_file.write(message + "\n")
        log_file.flush()

def log_stderr(message):
    print(message, file=sys.stderr)
    if log_file:
        log_file.write(message + "\n")
        log_file.flush()

def ping_db():
    connect = None
    cursor = None
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        connect = psycopg2.connect(
            host=config["host"],
            port=config["port"],
            dbname=config["database"],
            user=user,
            password=password,
            connect_timeout=connect_timeout
        )
        cursor = connect.cursor()
        cursor.execute("SELECT VERSION();")
        version = cursor.fetchone()

        if version and isinstance(version[0], str) and version[0].startswith("PostgreSQL"):
            log_stdout(f"[{timestamp}] Успешное подключение. Версия PostgreSQL: {version[0]}")
        else:
            log_stdout(f"[{timestamp}] Не типичный ответ на SELECT VERSION(): {version}")

    except psycopg2.OperationalError as e:
        if 'password authentication failed' in str(e):
            log_stderr(f"[{timestamp}] Ошибка: неверный пароль или логин.")
        else:
            log_stderr(f"[{timestamp}] Ошибка подключения к базе данных: {e}")

    except psycopg2.Error as e:
        log_stderr(f"[{timestamp}] Произошла ошибка с базой данных: {e}")

    except UnicodeDecodeError as e:
        log_stderr(f"[{timestamp}] Произошла ошибка UnicodeDecodeError: {e}")

    except Exception as e:
        log_stderr(f"[{timestamp}] Неизвестная ошибка: {e}")

    finally:
        if cursor:
            cursor.close()
        if connect:
            connect.close()

if __name__ == "__main__":
    print(f"Сервис запущен. Опрос каждые {interval // 60} минут.")
    while True:
        ping_db()
        time.sleep(interval)