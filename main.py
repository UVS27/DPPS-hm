import os
import sys
import json
import getpass
import psycopg2
from psycopg2 import sql
import logging


# with open("config_os.json", "r", encoding="utf-8") as f:
with open("config_docker.json", "r", encoding="utf-8") as f:
    config = json.load(f)


def insert_single_record(cursor, table_name, return_mode = False, auto_fk=None):
    """
    Добавляет одну запись в указанную таблицу.
    return_mode: булева, дополняющая query RETURNING id;
    auto_fk: dict {column_name: value} — значения для колонок, которые не спрашиваются у пользователя.
    :return: query, params
    """
    columns_info = get_table_columns_info(cursor, table_name)
    columns = []
    params = []
    pk_column = None

    for col in columns_info:
        col_name = col["column_name"]
        has_default = col["has_default"]
        is_primary = col["is_primary"]

        if auto_fk and col_name in auto_fk:
            value = auto_fk[col_name]
            columns.append(col_name)
            params.append(value)
            continue

        # --- Случай: PRIMARY KEY с DEFAULT ---
        if is_primary and has_default:
            print(f"\n{col_name} — PRIMARY KEY. У этой колонки есть DEFAULT (автогенерация).")
            print("Желаете ли вы заполнить id вручную?")
            pk_column = col_name

            hand = input("(y/N): ").strip().lower()
            while True:
                if hand == "y":
                    value = input("Введите значение: ").strip()
                    if value == "":
                        print("Вы не можете выбрать NULL для PRIMARY KEY! Повторите попытку.")
                        continue
                    try:
                        value = int(value)
                    except ValueError:
                        print("Ошибка: значение должно быть числом.")
                        continue
                    columns.append(col_name)
                    params.append(value)
                    break
                else:
                    break

        # --- Случай: любая колонка с DEFAULT (но не PK) ---
        elif has_default:
            print(f"У {col_name} колонки есть DEFAULT (автогенерация).")
            print("Желаете ли вы заполнить её вручную?")
            hand = input("(y/N): ").strip().lower()
            while True:
                if hand == "y":
                    value = input(f"Введите значение для колонки {col_name} (Enter = NULL): ").strip()
                    if value == "":
                        value = None
                    columns.append(col_name)
                    params.append(value)
                    break
                else:
                    break

        # --- Случай: колонка без DEFAULT ---
        else:
            value = input(f"Введите значение для колонки {col_name} (Enter = NULL): ").strip()
            if value == "":
                value = None
            columns.append(col_name)
            params.append(value)

    # Добавление записи
    col_identifiers = [sql.Identifier(c) for c in columns]
    placeholders = [sql.Placeholder()] * len(columns)

    query = sql.SQL("INSERT INTO {table} ({fields}) VALUES ({values})").format(
        table=sql.Identifier(table_name),
        fields=sql.SQL(", ").join(col_identifiers),
        values=sql.SQL(", ").join(placeholders)
    )

    if return_mode:
        query = query + sql.SQL(" RETURNING {pk}").format(pk=sql.Identifier(pk_column))

    return query, params

def main():
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

        query = sql.SQL("""
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = %s
                        ORDER BY {order_col};
                        """).format(
            order_col=sql.Identifier("table_name")
        )
        cursor.execute(query, ("public",))
        tables = cursor.fetchall()

        print("\nДоступные таблицы:")
        for i, tbl in enumerate(tables, start=1):
            print(f"{i}. {tbl[0]}")

        choice = input("\nВведите номер таблицы для действий: ")
        try:
            choice = int(choice)
            if 1 <= choice <= len(tables):
                table_name = tables[choice - 1][0]
            else:
                log_error("Ошибка: выбор несуществующей таблицы.")
                sys.exit()
        except (ValueError, IndexError):
            log_error("Ошибка: некорректный ввод.")
            sys.exit()

        # Меню операций
        print("\nВыберите действие:")
        print("1. Просмотр (без фильтрации)")
        print("2. Просмотр (с фильтрацией по одному значению)")
        print("3. Просмотр (с фильтрацией по нескольким значениям)")
        print("4. Обновить одну запись")
        print("5. Обновить несколько записей")
        print("6. Добавление записи в одну таблицу")
        print("7. Добавление записи в несколько связанных таблиц")
        print("8. Множественное добавление записи в одну таблицу")

        mode = input("Ваш выбор: ")
        params = []
        queries = []  # список всех INSERT-запросов
        query = ""

        # 6.1.1 Без фильтрации - ✅
        if mode == "1":
            query = sql.SQL("SELECT * FROM {table}").format(
                table=sql.Identifier(table_name)
            )
        # 6.1.2. Фильтрация по одному значению - ✅
        elif mode == "2":
            print("\nВыберите колонку фильтрации")
            column = choose_columns(cursor, table_name, True)
            value = input(f"Введите значение для поиска в {column}: ")
            query = sql.SQL("SELECT * FROM {table} WHERE {col} = %s;").format(
                col=sql.Identifier(column),
                table=sql.Identifier(table_name)
            )
            params.append(value)
        # 6.1.3. Фильтрация по нескольким значениям - ✅
        elif mode == "3":
            conditions_sql = []
            print("\nВведите пары <колонка фильтрации → значение фильтрации>."
                  "\nНажмите Enter при выборе колонки, если хотите закончить."
                  "\nНажмите Enter при выборе значения, чтобы выбрать условием NULL.\n")

            while True:
                print("Выберите колонку для фильтрации")
                column = choose_columns(cursor, table_name, False)
                if column is None:
                    break
                col = sql.Identifier(column)

                value = input(f"Введите значение для {column}: ")

                if value == "":
                    conditions_sql.append(sql.SQL("{} IS NULL").format(col))
                else:
                    conditions_sql.append(sql.SQL("{} = %s").format(col))
                    params.append(value)
                print()

            if conditions_sql:
                query = sql.SQL("SELECT * FROM {table} WHERE {conditions};").format(
                    table=sql.Identifier(table_name),
                    conditions=sql.SQL(" AND ").join(conditions_sql)
                )
        # 6.2.1 Обновление одной записи (нельзя менять id, которое PRIMARY KEY) - ✅
        elif mode == "4":
            pk_column = get_primary_key_column(cursor, table_name)

            if not pk_column:
                log_error(f"Ошибка: в таблице {table_name} нет PRIMARY KEY!")
                sys.exit()
            else:
                print(f"\nНайдена PRIMARY_KEY колонка: {pk_column}")

            query_pk = sql.SQL("SELECT {pk} FROM {table} ORDER BY {pk}").format(
                pk=sql.Identifier(pk_column),
                table=sql.Identifier(table_name)
            )
            cursor.execute(query_pk)
            available_ids = [str(row[0]) for row in cursor.fetchall()]

            if available_ids:
                if len(available_ids) > config.get("limit_rows", 25):
                    shown_ids = available_ids[:5] + ["..."] + available_ids[-5:]
                    print(f"Доступные id на выбор ({len(available_ids)} всего): {', '.join(shown_ids)}")
                else:
                    print(f"Доступные id на выбор: {', '.join(available_ids)}")
            else:
                log_error(f"Таблица {table_name} пуста!")
                sys.exit()

            # Пользователь выбирает одно из доступных значений
            while True:
                id_value = input(f"\nВведите значение для {pk_column}: ")
                if id_value in available_ids:
                    break
                else:
                    log_error("Ошибка: такого значения нет. Повторите ввод.")

            updates_sql = []
            params = []
            # Запрос обновления
            print("Введите пары <колонка → новое значение>. "
                  "\nНажмите Enter при выборе колонки, если хотите закончить. "
                  "\nНажмите Enter при выборе значения, чтобы выбрать NULL.\n")

            while True:
                column = choose_columns(cursor, table_name, False)
                if column is None:  # Enter = конец обновлений
                    break
                if column == pk_column:
                    print("Нельзя обновлять PRIMARY_KEY колонку. Повторите выбор.\n")
                    continue

                value = input(f"Новое значение для {column}: ")
                col = sql.Identifier(column)
                if value == "":
                    updates_sql.append(sql.SQL("{} = NULL").format(col))
                else:
                    updates_sql.append(sql.SQL("{} = %s").format(col))
                    params.append(value)
                print()

            if not updates_sql:
                log_error("Обновление отменено: ни одно поле не выбрано.")
                sys.exit()

            params.append(id_value)
            query = sql.SQL("UPDATE {table} SET {updates} WHERE {pk} = %s").format(
                table=sql.Identifier(table_name),
                updates=sql.SQL(", ").join(updates_sql),
                pk=sql.Identifier(pk_column)
            )
        # 6.2.2 Массовое обновление - ✅
        elif mode == "5":
            pk_column = get_primary_key_column(cursor, table_name)

            if not pk_column:
                log_error(f"Ошибка: в таблице {table_name} нет PRIMARY KEY!")
                sys.exit()
            else:
                print(f"\nНайдена PRIMARY_KEY колонка: {pk_column}")

            print("\nВыберите колонку для обновления")
            while True:
                column_to_update = choose_columns(cursor, table_name, True)
                if column_to_update == pk_column:
                    print("Нельзя обновлять PRIMARY_KEY колонку. Повторите выбор.\n")
                    continue
                else:
                    break

            new_value = input("Введите новое значение (Enter = NULL): ")

            print("\nВыберите колонку для фильтрации")
            filter_column = choose_columns(cursor, table_name, True)
            filter_values = [v.strip() for v in input(
                "Введите значения через запятую (не добавляйте пробел, если его нет в названии!): "
            ).split(",")]

            up_column = sql.Identifier(column_to_update)

            if new_value == "":
                updates_sql = sql.SQL("{} = NULL").format(up_column)
                params = filter_values
            else:
                updates_sql = sql.SQL("{} = %s").format(up_column)
                params = [new_value] + filter_values

            query = sql.SQL(
                "UPDATE {table} SET {updates} WHERE {filter} IN ({placeholders})"
            ).format(
                table=sql.Identifier(table_name),
                updates=updates_sql,
                filter=sql.Identifier(filter_column),
                placeholders=sql.SQL(", ").join(sql.Placeholder() * len(filter_values))
            )

        # 6.3.1 Добавление новой строки - ✅
        elif mode == "6":
            print("\nСоздание новой записи:")
            query, params = insert_single_record(cursor, table_name)

        # 6.3.2 Добавление новой строки в несколько таблиц - ✅
        elif mode == "7":
            query, params = insert_single_record(cursor, table_name, True)
            cursor.execute(query, tuple(params))
            connect.commit()
            inserted_id = cursor.fetchone()[0]

            refs = get_referencing_foreign_keys(cursor, table_name)
            if not refs:
                log_error(f"Связанных таблиц не найдено. Добавлена одна запись в {table_name}.")
                sys.exit()

            print("\nНайдены связанные таблицы:")
            for i, r in enumerate(refs, 1):
                print(f"{i}. {r['table']} (ссылается по колонке {r['column']})")

            selected = input("Введите номера таблиц для добавления записей через запятую (например: 1,3): ").strip()
            selected = [int(x) for x in selected.split(",") if x.strip().isdigit()]

            for idx in selected:
                if 1 <= idx <= len(refs):
                    ref = refs[idx - 1]

                    query, params = insert_single_record(cursor, ref['table'], auto_fk={ref['column']: inserted_id})
                    cursor.execute(query, tuple(params))

        # 6.4 Вставка нескольких строк в одну таблицу - ✅
        elif mode == "8":
            print("\nСоздание записей в таблице:", table_name)

            while True:
                query, params = insert_single_record(cursor, table_name)
                queries.append((query, tuple(params)))
                again = input("Добавить ещё запись? (y/N): ").strip().lower()
                if again not in ("y", "yes"):
                    break

            for q, p in queries:
                cursor.execute(q, p)
        else:
            log_error("Ошибка: выбран несуществующий режим.")
            sys.exit()


        if query == "":
            log_info(f"\nЗапрос {mode} был прерван.")
            sys.exit()

        # Выполнение запроса
        log_info(f"\nРезультат запроса {mode}:")
        if mode in ["1", "2", "3", "4", "5", "6"]:
            cursor.execute(query, tuple(params))

        # Если это действие задания из 6.1, то делаем простенькую печатную версию таблицы
        if mode in ["1", "2", "3"]:
            rows = cursor.fetchall()
            col_names = [desc[0] for desc in cursor.description]

            log_info("-" * 40)
            log_info(f"Таблица: {table_name}")
            log_info("-" * 40)

            # --- обработка колонок ---
            if len(col_names) > config.get("limit_table_columns", 15):
                shown_cols = col_names[:5] + ["..."] + col_names[-5:]
                col_mapping = list(range(5)) + [-1] + list(range(len(col_names) - 5, len(col_names)))
            else:
                shown_cols = col_names
                col_mapping = list(range(len(col_names)))

            log_info(" | ".join(shown_cols))
            log_info("-" * 40)

            # --- обработка строк ---
            if len(rows) > config.get("limit_table_rows", 15):
                shown_rows = rows[:5] + ["..."] + rows[-5:]
            else:
                shown_rows = rows

            for row in shown_rows:
                if row == "...":
                    log_info("...")
                else:
                    if len(col_names) > config.get("limit_table_columns", 15):
                        row_display = [str(row[i]) if row[i] is not None else "NULL" for i in range(5)]
                        row_display += ["..."]
                        row_display += [str(row[i]) if row[i] is not None else "NULL" for i in
                                        range(len(row) - 5, len(row))]
                    else:
                        row_display = [str(x) if x is not None else "NULL" for x in row]

                    log_info(" | ".join(row_display))
        # В остальных случаях выполняем коммит
        else:
            connect.commit()
            log_info(f"Обновление данных {table_name} выполнено успешно.")

    except psycopg2.OperationalError as e:
        if 'password authentication failed' in str(e):
            log_error("Ошибка: неверный пароль или логин.")
        else:
            log_error("Ошибка подключения к базе данных.", e)

    except psycopg2.Error as e:
        log_error("Произошла ошибка с базой данных.", e)

    except UnicodeDecodeError as e:
        log_error("Произошла ошибка UnicodeDecodeError.", e)

    finally:
        if cursor:
            cursor.close()
        if connect:
            connect.close()


def get_referencing_foreign_keys(cursor, table_name, schema='public'):
    """
    Находит все внешние ключи, которые ссылаются на таблицу table_name.
    Возвращает список словарей:
    [
        {
            'table': 'referencing_table',
            'column': 'column_in_referencing_table',
            'constraint_name': 'fk_constraint_name',
            'referenced_column': 'column_in_this_table',
            'on_delete': 'CASCADE/SET NULL/NO ACTION',
            'on_update': 'CASCADE/SET NULL/NO ACTION'
        },
        ...
    ]
    """
    query = """
        SELECT 
            kcu.table_name  AS referencing_table,
            kcu.column_name AS referencing_column,
            ccu.column_name AS referenced_column,
            tc.constraint_name,
            rc.update_rule  AS on_update,
            rc.delete_rule  AS on_delete
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
            ON tc.constraint_name = kcu.constraint_name
           AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
            ON ccu.constraint_name = tc.constraint_name
           AND ccu.table_schema = tc.table_schema
        JOIN information_schema.referential_constraints AS rc
            ON rc.constraint_name = tc.constraint_name
           AND rc.constraint_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND ccu.table_name = %s
          AND ccu.table_schema = %s;
    """
    cursor.execute(query, (table_name, schema))
    refs = []
    for row in cursor.fetchall():
        refs.append({
            'table': row[0],
            'column': row[1],
            'referenced_column': row[2],
            'constraint_name': row[3],
            'on_update': row[4],
            'on_delete': row[5]
        })
    return refs


def get_primary_key_column(cursor, table_name):
    """
    Определяет колонку таблицы с PRIMARY_KEY и возвращает ее, если нашла
    """
    cursor.execute("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
          AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_name = %s;
    """, (table_name,))
    result = cursor.fetchone()
    if result:
        return result[0]
    else:
        return None


def get_table_columns_info(cursor, table_name):
    """
    Анализирует все колонки таблицы и возвращает список с информацией:
    - column_name: имя колонки
    - has_default: True/False (есть ли значение по умолчанию)
    - is_primary: True/False
    """
    query = """
        SELECT 
            c.column_name,
            c.column_default,
            MAX(CASE WHEN tc.constraint_type = 'PRIMARY KEY' THEN 1 ELSE 0 END) AS is_primary,
            c.ordinal_position
        FROM information_schema.columns c
        LEFT JOIN information_schema.key_column_usage kcu
            ON c.table_name = kcu.table_name AND c.column_name = kcu.column_name
        LEFT JOIN information_schema.table_constraints tc
            ON tc.constraint_name = kcu.constraint_name AND tc.table_name = c.table_name
        WHERE c.table_name = %s
        GROUP BY c.column_name, c.column_default, c.ordinal_position
        ORDER BY c.ordinal_position;
    """
    cursor.execute(query, (table_name,))

    columns_info = []
    for row in cursor.fetchall():
        columns_info.append({
            "column_name": row[0],
            "has_default": True if row[1] is not None else False,
            "is_primary": bool(row[2])
        })
    return columns_info


def get_columns(cursor, table_name):
    """
    Возвращает список колонок таблицы в порядке их расположения.
    """
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = %s
        ORDER BY ordinal_position;
    """, (table_name,))
    return [row[0] for row in cursor.fetchall()]


def choose_columns(cursor, table_name, repeater=True):
    """
    Универсальный выбор колонок пользователем.
    Позволяет вводить номер до тех пор, пока пользователь не введёт корректное значение.
    repeater: Необходим для логики с NULL
    """
    columns = get_columns(cursor, table_name)

    print("Доступные колонки:")
    if len(columns) > config.get("limit_columns", 25):
        for i, col in enumerate(columns[:5], start=1):
            print(f"{i}. {col}")
        print("...")
        for i, col in enumerate(columns[-5:], start=len(columns) - 4):
            print(f"{i}. {col}")
    else:
        for i, col in enumerate(columns, start=1):
            print(f"{i}. {col}")

    while True:
        try:
            num = int(input("Введите номер колонки: "))
            if 1 <= num <= len(columns):
                return columns[num - 1]
            else:
                print(f"Ошибка: введите число от 1 до {len(columns)}\n")
        except ValueError:
            if repeater:
                print("Ошибка: нужно ввести число.\n")
            else:
                return None


USER_LOG_PATH = os.environ.get("LOG_USER_FILE_PATH")
FULL_LOG_PATH = os.environ.get("LOG_FULL_FILE_PATH")

if USER_LOG_PATH and FULL_LOG_PATH:
    try:
        os.makedirs(os.path.dirname(USER_LOG_PATH), exist_ok=True)
    except Exception as e:
        print(f"Ошибка в получении пути файла логирования для пользователя: {e}", file=sys.stderr)
    try:
        os.makedirs(os.path.dirname(FULL_LOG_PATH), exist_ok=True)
    except Exception as e:
        print(f"Ошибка в получении пути файла логирования полной версии: {e} ", file=sys.stderr)
else:
    print("Ошибка! Пути к файлам логирования пусты!")
    sys.exit(1)

# --- Логгер для пользователя (консоль + logging.log) ---
user_logger = logging.getLogger("user")
user_logger.setLevel(logging.INFO)
# Формат дружественный пользователю
user_formatter = logging.Formatter("%(message)s")

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(user_formatter)
user_logger.addHandler(console_handler)

user_file_handler = logging.FileHandler(USER_LOG_PATH, mode="a", encoding="utf-8")
user_file_handler.setFormatter(user_formatter)
user_logger.addHandler(user_file_handler)

# --- Логгер для специалистов (full_logging.log) ---
full_logger = logging.getLogger("full")
full_logger.setLevel(logging.DEBUG)
# Формат с деталями
full_formatter = logging.Formatter(
    "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

full_file_handler = logging.FileHandler(FULL_LOG_PATH, mode="a", encoding="utf-8")
full_file_handler.setFormatter(full_formatter)
full_logger.addHandler(full_file_handler)


def log_info(message: str):
    """Дружественное сообщение пользователю"""
    user_logger.info(message)
    full_logger.info(message)


def log_error(user_message: str, exception: Exception = None):
    """Сообщение об ошибке: пользователю — дружелюбно, в полный лог — детали"""
    user_logger.error(user_message)
    if exception:
        full_logger.exception(exception)
    else:
        full_logger.error(user_message)


if __name__ == "__main__":
    main()