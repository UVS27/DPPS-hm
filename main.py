import json
import psycopg2
import getpass

with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

user = input("Введите имя пользователя: ")
password = getpass.getpass("Введите пароль: ")

connect = None
cursor = None

try:
    connect = psycopg2.connect(
        host=config["host"],
        port=config["port"],
        dbname=config["database"],
        user=user,
        password=password
    )
    cursor = connect.cursor()

    cursor.execute("SELECT VERSION();")
    version = cursor.fetchone()
    print(f"Версия PostgreSQL: {version[0]}")

except psycopg2.OperationalError as e:
    if 'password authentication failed' in str(e):
        print("Ошибка: неверный пароль или логин.")
    else:
        print("Ошибка подключения к базе данных:", e)

except psycopg2.Error as e:
    print("Произошла ошибка с базой данных:", e)

except UnicodeDecodeError as e:
    print("Произошла ошибка UnicodeDecodeError:", e)

finally:
    if cursor:
        cursor.close()
    if connect:
        connect.close()
