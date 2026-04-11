import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import psycopg2
from psycopg2 import errors
from psycopg2 import sql
from itsdangerous import BadSignature
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)


def create_app() -> Flask:
    """Веб‑интерфейс к Postgres: login → tables → view/add/edit/delete."""
    app = Flask(__name__)
    # Нужен для сессии. В Docker лучше задать APP_SECRET_KEY, чтобы не "вылетало" после рестарта.
    app.secret_key = os.environ.get("APP_SECRET_KEY") or secrets.token_hex(32)

    app.config["DB_HOST"] = os.environ.get("DB_HOST", "db")
    app.config["DB_PORT"] = int(os.environ.get("DB_PORT", "5432"))
    app.config["DB_NAME"] = os.environ.get("DB_NAME", "software_company_db")
    app.config["SESSION_TIMEOUT_MINUTES"] = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "10"))

    # Сколько можно бездействовать.
    app.permanent_session_lifetime = timedelta(minutes=app.config["SESSION_TIMEOUT_MINUTES"])

    # Логи пишем в /app/logs.
    _configure_logging(app)

    @app.before_request
    def _start_timer():
        # Для логов: время запроса.
        request._start_time = time.time()

    @app.teardown_request
    def _log_exception(exc):
        # Пишем любые падения (это место отрабатывает почти всегда).
        if exc is None:
            return
        logging.getLogger("full").error(
            "Request exception method=%s path=%s user=%s",
            getattr(request, "method", "-"),
            getattr(request, "path", "-"),
            session.get("db_user") if session else "-",
            exc_info=exc,
        )
        logging.getLogger("user").error(
            "Ошибка: %s %s (%s)",
            getattr(request, "method", "-"),
            getattr(request, "path", "-"),
            type(exc).__name__,
        )

    @app.after_request
    def _log_request(response):
        try:
            duration_ms = int((time.time() - getattr(request, "_start_time", time.time())) * 1000)
        except Exception:
            duration_ms = -1
        user = session.get("db_user") or "-"
        app.logger.info("%s %s -> %s (%sms) user=%s", request.method, request.path, response.status_code, duration_ms, user)
        logging.getLogger("full").debug(
            "req method=%s path=%s status=%s duration_ms=%s user=%s ip=%s ua=%s",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            user,
            request.headers.get("X-Forwarded-For", request.remote_addr),
            request.headers.get("User-Agent", "-"),
        )
        return response

    @app.errorhandler(BadSignature)
    def _bad_signature(_err):
        # Сессия "сломалась" (например, сменился APP_SECRET_KEY) → просим войти заново.
        session.clear()
        flash("Сеанс недействителен. Войдите снова.", "error")
        return redirect(url_for("login"))

    @app.errorhandler(Exception)
    def _unhandled_error(err):
        # Любая непойманная ошибка: в full лог — детали.
        logging.getLogger("full").exception("Unhandled error: %s", err)
        logging.getLogger("user").error("Ошибка приложения: %s", err)
        return (
            render_template("error.html", message="Внутренняя ошибка приложения. Проверьте логи."),
            500,
        )

    @app.before_request
    def _enforce_session_timeout():
        """Если долго ничего не делать — просим войти заново."""
        # Разрешаем страницу логина/логаута и статику без проверки
        if request.endpoint in {"login", "login_post", "logout"}:
            return None
        if request.endpoint == "static":
            return None

        if not _has_db_creds():
            return None

        now = datetime.now(timezone.utc)
        last_active_iso = session.get("last_active")
        if last_active_iso:
            try:
                last_active = datetime.fromisoformat(last_active_iso)
            except Exception:
                last_active = None
            if last_active and now - last_active > app.permanent_session_lifetime:
                session.clear()
                flash("Сеанс истёк (10 минут бездействия). Войдите снова.", "error")
                return redirect(url_for("login"))

        session["last_active"] = now.isoformat()
        session.permanent = True

    @app.get("/")
    def index():
        # Если не вошли — /login, иначе /tables.
        if not _has_db_creds():
            return redirect(url_for("login"))
        return redirect(url_for("tables"))

    @app.get("/login")
    def login():
        return render_template("login.html")

    @app.post("/login")
    def login_post():
        # Проверяем логин/пароль подключением к БД.
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        if not username or not password:
            flash("Введите логин и пароль.", "error")
            return redirect(url_for("login"))

        try:
            _test_connection(username, password)
        except psycopg2.OperationalError:
            logging.getLogger("user").warning("Login failed for user=%s", username)
            flash("Неверный логин или пароль. Попробуйте ещё раз.", "error")
            return redirect(url_for("login"))
        except Exception:
            logging.getLogger("full").exception("Login error for user=%s", username)
            flash("Не удалось подключиться к БД. Проверьте доступность Postgres.", "error")
            return redirect(url_for("login"))

        session["db_user"] = username
        session["db_pass"] = password
        session["last_active"] = datetime.now(timezone.utc).isoformat()
        session.permanent = True
        logging.getLogger("user").info("Login ok for user=%s", username)
        return redirect(url_for("tables"))

    @app.post("/logout")
    def logout():
        # Выход.
        logging.getLogger("user").info("Logout user=%s", session.get("db_user"))
        session.clear()
        return redirect(url_for("login"))

    @app.get("/tables")
    def tables():
        # Список таблиц.
        if not _has_db_creds():
            return redirect(url_for("login"))

        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE'
                ORDER BY table_name;
                """,
                ("public",),
            )
            table_names = [r[0] for r in cur.fetchall()]

        return render_template("tables.html", table_names=table_names)

    @app.get("/table/<table_name>/view")
    def table_view(table_name: str):
        # Просмотр таблицы (первые 200 строк).
        if not _has_db_creds():
            return redirect(url_for("login"))

        with _connect() as conn, conn.cursor() as cur:
            cols = _get_columns(cur, table_name)
            pk = _get_primary_key_column(cur, table_name)
            pk_idx = cols.index(pk) if pk and pk in cols else None
            cur.execute(
                sql.SQL("SELECT * FROM {t} ORDER BY 1 LIMIT 200").format(t=sql.Identifier(table_name))
            )
            rows = cur.fetchall()

        return render_template(
            "table_view.html",
            table_name=table_name,
            columns=cols,
            rows=rows,
            pk=pk,
            pk_idx=pk_idx,
        )

    @app.get("/table/<table_name>/edit/<pk_value>")
    def table_edit(table_name: str, pk_value: str):
        # GET-форма редактирования по PK.
        if not _has_db_creds():
            return redirect(url_for("login"))

        with _connect() as conn, conn.cursor() as cur:
            pk = _get_primary_key_column(cur, table_name)
            if not pk:
                flash("Редактирование недоступно: в таблице нет PRIMARY KEY.", "error")
                return redirect(url_for("table_view", table_name=table_name))

            pk_type = _get_column_data_type(cur, table_name, pk) or "text"
            try:
                pk_typed = _coerce_value(pk_value, pk_type)
            except ValueError:
                flash("Некорректный идентификатор строки.", "error")
                return redirect(url_for("table_view", table_name=table_name))

            cols = _get_columns(cur, table_name)
            columns_info = _get_columns_info(cur, table_name)
            # GENERATED columns редактировать бессмысленно (их считает БД).
            editable_columns = [c for c in columns_info if not c.is_generated]

            cur.execute(
                sql.SQL("SELECT * FROM {t} WHERE {pk} = %s LIMIT 1").format(
                    t=sql.Identifier(table_name),
                    pk=sql.Identifier(pk),
                ),
                (pk_typed,),
            )
            row = cur.fetchone()
            if not row:
                flash("Строка не найдена.", "error")
                return redirect(url_for("table_view", table_name=table_name))

            row_dict = {cols[i]: row[i] for i in range(len(cols))}

        return render_template(
            "table_edit.html",
            table_name=table_name,
            pk=pk,
            pk_value=pk_value,
            columns=editable_columns,
            row=row_dict,
        )

    @app.post("/table/<table_name>/edit/<pk_value>")
    def table_edit_post(table_name: str, pk_value: str):
        # POST-обновление строки с проверками типов/NOT NULL/FK/UNIQUE и ограничениями на смену PK.
        if not _has_db_creds():
            return redirect(url_for("login"))

        with _connect() as conn, conn.cursor() as cur:
            pk = _get_primary_key_column(cur, table_name)
            if not pk:
                flash("Редактирование недоступно: в таблице нет PRIMARY KEY.", "error")
                return redirect(url_for("table_view", table_name=table_name))

            pk_type = _get_column_data_type(cur, table_name, pk) or "text"
            try:
                old_pk = _coerce_value(pk_value, pk_type)
            except ValueError:
                flash("Некорректный идентификатор строки.", "error")
                return redirect(url_for("table_view", table_name=table_name))

            cols = _get_columns(cur, table_name)
            columns_info = _get_columns_info(cur, table_name)
            editable_columns = [c for c in columns_info if not c.is_generated]
            outgoing_fks = _get_outgoing_foreign_keys(cur, table_name)
            fk_by_from_column = {fk.from_column: fk for fk in outgoing_fks}
            unique_constraints = _get_unique_constraints(cur, table_name)

            # Загружаем текущую строку (для сравнения/исключения)
            cur.execute(
                sql.SQL("SELECT * FROM {t} WHERE {pk} = %s LIMIT 1").format(
                    t=sql.Identifier(table_name),
                    pk=sql.Identifier(pk),
                ),
                (old_pk,),
            )
            current = cur.fetchone()
            if not current:
                flash("Строка не найдена.", "error")
                return redirect(url_for("table_view", table_name=table_name))

            new_values: dict[str, Any] = {}
            errors_list: list[str] = []

            for c in editable_columns:
                raw = request.form.get(c.name, "")
                raw = "" if raw is None else raw.strip()

                if raw == "":
                    if not c.nullable:
                        errors_list.append(f"Колонка «{c.name}» обязательна (NOT NULL).")
                    new_values[c.name] = None
                    continue

                try:
                    new_values[c.name] = _coerce_value(raw, c.data_type)
                except ValueError as e:
                    errors_list.append(f"Колонка «{c.name}»: {e}")

            # FK-проверка: нельзя ставить несуществующие id в внешние ключи
            for col, v in list(new_values.items()):
                if v is None:
                    continue
                fk = fk_by_from_column.get(col)
                if not fk:
                    continue
                if not _fk_target_exists(cur, fk, v):
                    errors_list.append(
                        f"Колонка «{col}»: значение {v} не существует в таблице «{fk.to_table}» (колонка «{fk.to_column}»)."
                    )

            # Проверка PK при изменении
            new_pk = new_values.get(pk, old_pk)
            if new_pk != old_pk:
                # 1) нельзя поставить занятый id
                cur.execute(
                    sql.SQL("SELECT 1 FROM {t} WHERE {pk} = %s LIMIT 1").format(
                        t=sql.Identifier(table_name),
                        pk=sql.Identifier(pk),
                    ),
                    (new_pk,),
                )
                if cur.fetchone():
                    errors_list.append(f"Нельзя установить {pk}={new_pk}: такое значение уже существует.")

                # 2) нельзя менять id, если на него есть ссылки
                incoming = _get_incoming_foreign_keys(cur, table_name)
                for fk in incoming:
                    cur.execute(
                        sql.SQL("SELECT 1 FROM {from_table} WHERE {from_col} = %s LIMIT 1").format(
                            from_table=sql.Identifier(fk.from_table),
                            from_col=sql.Identifier(fk.from_column),
                        ),
                        (old_pk,),
                    )
                    if cur.fetchone():
                        errors_list.append(
                            f"Нельзя менять {table_name}.{pk}={old_pk}: есть ссылки в {fk.from_table}.{fk.from_column}."
                        )
                        break

            # Проверка UNIQUE (кроме текущей строки)
            for uc_name, uc_cols in unique_constraints.items():
                # уникальность с NULL: если хотя бы одно поле NULL — пропускаем
                vals = [new_values.get(c) for c in uc_cols]
                if any(v is None for v in vals):
                    continue
                where_parts = [sql.SQL("{c} = %s").format(c=sql.Identifier(c)) for c in uc_cols]
                q = sql.SQL("SELECT 1 FROM {t} WHERE {conds} AND {pk} <> %s LIMIT 1").format(
                    t=sql.Identifier(table_name),
                    conds=sql.SQL(" AND ").join(where_parts),
                    pk=sql.Identifier(pk),
                )
                cur.execute(q, (*vals, old_pk))
                if cur.fetchone():
                    errors_list.append(
                        f"Нарушение UNIQUE ({uc_name}): комбинация полей {', '.join(uc_cols)} уже используется."
                    )

            if errors_list:
                for e in errors_list:
                    flash(e, "error")
                logging.getLogger("user").warning(
                    "Update blocked table=%s user=%s pk=%s errors=%s",
                    table_name,
                    session.get("db_user"),
                    old_pk,
                    errors_list,
                )
                row_dict = {cols[i]: current[i] for i in range(len(cols))}
                return (
                    render_template(
                        "table_edit.html",
                        table_name=table_name,
                        pk=pk,
                        pk_value=pk_value,
                        columns=editable_columns,
                        row=row_dict,
                    ),
                    400,
                )

            # UPDATE (только позиционные параметры, без именованных плейсхолдеров)
            set_cols = list(new_values.keys())
            set_parts = [sql.SQL("{c} = %s").format(c=sql.Identifier(c)) for c in set_cols]
            q = sql.SQL("UPDATE {t} SET {sets} WHERE {pk} = %s").format(
                t=sql.Identifier(table_name),
                sets=sql.SQL(", ").join(set_parts),
                pk=sql.Identifier(pk),
            )
            params = [new_values[c] for c in set_cols] + [old_pk]
            cur.execute(q, params)

            try:
                conn.commit()
                logging.getLogger("user").info(
                    "Update ok table=%s user=%s old_pk=%s new_pk=%s",
                    table_name,
                    session.get("db_user"),
                    old_pk,
                    new_values.get(pk, old_pk),
                )
            except psycopg2.IntegrityError as e:
                conn.rollback()
                logging.getLogger("full").exception("Update IntegrityError table=%s old_pk=%s values=%s", table_name, old_pk, new_values)
                flash(f"БД отклонила обновление (IntegrityError): {getattr(e, 'pgerror', None) or str(e)}", "error")
                return redirect(url_for("table_edit", table_name=table_name, pk_value=pk_value))

        flash("Строка обновлена.", "success")
        return redirect(url_for("table_view", table_name=table_name))

    @app.get("/table/<table_name>/add")
    def table_add(table_name: str):
        # Форма добавления: выводим только "ручные" колонки (без DEFAULT/IDENTITY/GENERATED).
        if not _has_db_creds():
            return redirect(url_for("login"))

        with _connect() as conn, conn.cursor() as cur:
            columns = _get_columns_info(cur, table_name)
            form_columns = [c for c in columns if not c.auto_generated]

        return render_template("table_add.html", table_name=table_name, columns=form_columns)

    @app.post("/table/<table_name>/add")
    def table_add_post(table_name: str):
        # Вставка строки: сначала наши проверки, потом INSERT.
        if not _has_db_creds():
            return redirect(url_for("login"))

        with _connect() as conn, conn.cursor() as cur:
            columns = _get_columns_info(cur, table_name)
            form_columns = [c for c in columns if not c.auto_generated]
            outgoing_fks = _get_outgoing_foreign_keys(cur, table_name)
            fk_by_from_column = {fk.from_column: fk for fk in outgoing_fks}

            values: dict[str, Any] = {}
            errors: list[str] = []

            for c in form_columns:
                raw = request.form.get(c.name, "")
                raw = "" if raw is None else raw.strip()

                if raw == "":
                    if not c.nullable:
                        errors.append(f"Колонка «{c.name}» обязательна (NOT NULL).")
                    else:
                        values[c.name] = None
                    continue

                try:
                    values[c.name] = _coerce_value(raw, c.data_type)
                except ValueError as e:
                    errors.append(f"Колонка «{c.name}»: {e}")

            # FK-проверка: нельзя вставлять несуществующие id в внешние ключи
            for col, v in list(values.items()):
                if v is None:
                    continue
                fk = fk_by_from_column.get(col)
                if not fk:
                    continue
                if not _fk_target_exists(cur, fk, v):
                    errors.append(
                        f"Колонка «{col}»: значение {v} не существует в таблице «{fk.to_table}» (колонка «{fk.to_column}»)."
                    )

            if errors:
                for e in errors:
                    flash(e, "error")
                logging.getLogger("user").warning("Insert blocked table=%s user=%s errors=%s", table_name, session.get("db_user"), errors)
                return render_template("table_add.html", table_name=table_name, columns=form_columns), 400

            if not values:
                flash("Нет данных для добавления.", "error")
                return redirect(url_for("table_add", table_name=table_name))

            # INSERT: идентификаторы через Identifier, значения через параметры → нет SQL-инъекций.
            col_idents = [sql.Identifier(k) for k in values.keys()]
            placeholders = [sql.Placeholder(k) for k in values.keys()]
            q = sql.SQL("INSERT INTO {t} ({cols}) VALUES ({vals})").format(
                t=sql.Identifier(table_name),
                cols=sql.SQL(", ").join(col_idents),
                vals=sql.SQL(", ").join(placeholders),
            )
            try:
                cur.execute(q, values)
                conn.commit()
                logging.getLogger("user").info("Insert ok table=%s user=%s", table_name, session.get("db_user"))
            except psycopg2.IntegrityError as e:
                conn.rollback()
                # На всякий случай, если БД отклонит (FK/unique/not null/etc.)
                logging.getLogger("full").exception("Insert IntegrityError table=%s values=%s", table_name, values)
                flash(f"БД отклонила вставку (IntegrityError): {getattr(e, 'pgerror', None) or str(e)}", "error")
                return render_template("table_add.html", table_name=table_name, columns=form_columns), 400

        flash("Строка добавлена.", "success")
        return redirect(url_for("table_view", table_name=table_name))

    @app.get("/table/<table_name>/delete")
    def table_delete(table_name: str):
        # Удаление доступно только если есть PRIMARY KEY (иначе непонятно, что именно удаляем).
        if not _has_db_creds():
            return redirect(url_for("login"))

        with _connect() as conn, conn.cursor() as cur:
            pk = _get_primary_key_column(cur, table_name)
            cols = _get_columns(cur, table_name)
            rows: list[tuple[Any, ...]] = []
            pk_idx = cols.index(pk) if pk and pk in cols else 0
            if pk:
                cur.execute(
                    sql.SQL("SELECT * FROM {t} ORDER BY {pk} LIMIT 500").format(
                        t=sql.Identifier(table_name),
                        pk=sql.Identifier(pk),
                    )
                )
                rows = cur.fetchall()

        return render_template(
            "table_delete.html",
            table_name=table_name,
            pk=pk,
            pk_idx=pk_idx,
            columns=cols,
            rows=rows,
        )

    @app.post("/table/<table_name>/delete")
    def table_delete_post(table_name: str):
        # Удаление пачкой по PK. Перед удалением проверяем incoming FK, чтобы не нарушить ссылки.
        if not _has_db_creds():
            return redirect(url_for("login"))

        raw_ids = request.form.getlist("row_id")
        if not raw_ids:
            flash("Не выбрано ни одной строки.", "error")
            return redirect(url_for("table_delete", table_name=table_name))

        with _connect() as conn, conn.cursor() as cur:
            pk = _get_primary_key_column(cur, table_name)
            if not pk:
                flash("Удаление недоступно: в таблице нет PRIMARY KEY.", "error")
                return redirect(url_for("table_delete", table_name=table_name))

            pk_type = _get_column_data_type(cur, table_name, pk) or "text"
            try:
                ids = [_coerce_value(v, pk_type) for v in raw_ids]
            except ValueError:
                flash("Некорректные значения id для удаления.", "error")
                return redirect(url_for("table_delete", table_name=table_name))

            # Защита: нельзя удалять записи, на которые есть ссылки из других таблиц
            incoming = _get_incoming_foreign_keys(cur, table_name)
            blocked: dict[Any, list[str]] = {idv: [] for idv in ids}
            for fk in incoming:
                # Проверяем сразу пачкой через ANY
                cur.execute(
                    sql.SQL(
                        "SELECT {from_col}, COUNT(*) "
                        "FROM {from_table} "
                        "WHERE {from_col} = ANY(%s) "
                        "GROUP BY {from_col}"
                    ).format(
                        from_col=sql.Identifier(fk.from_column),
                        from_table=sql.Identifier(fk.from_table),
                    ),
                    (ids,),
                )
                for ref_value, cnt in cur.fetchall():
                    if cnt and ref_value in blocked:
                        blocked[ref_value].append(f"{fk.from_table}.{fk.from_column} ({cnt} шт.)")

            still_blocked = {k: v for k, v in blocked.items() if v}
            if still_blocked:
                # Покажем явно причину
                for idv, refs in list(still_blocked.items())[:10]:
                    flash(
                        f"Нельзя удалить {table_name}.{pk}={idv}: есть ссылки в {', '.join(refs)}",
                        "error",
                    )
                logging.getLogger("user").warning(
                    "Delete blocked table=%s user=%s ids=%s refs=%s",
                    table_name,
                    session.get("db_user"),
                    ids,
                    still_blocked,
                )
                if len(still_blocked) > 10:
                    flash(f"И ещё заблокированных строк: {len(still_blocked) - 10}.", "error")
                return redirect(url_for("table_delete", table_name=table_name))

            cur.execute(
                sql.SQL("DELETE FROM {t} WHERE {pk} = ANY(%s)").format(
                    t=sql.Identifier(table_name),
                    pk=sql.Identifier(pk),
                ),
                (ids,),
            )
            try:
                conn.commit()
                logging.getLogger("user").info("Delete ok table=%s user=%s ids=%s", table_name, session.get("db_user"), ids)
            except psycopg2.IntegrityError as e:
                conn.rollback()
                logging.getLogger("full").exception("Delete IntegrityError table=%s ids=%s", table_name, ids)
                flash(f"БД отклонила удаление (IntegrityError): {getattr(e, 'pgerror', None) or str(e)}", "error")
                return redirect(url_for("table_delete", table_name=table_name))

        flash(f"Удалено строк: {len(ids)}.", "success")
        return redirect(url_for("table_view", table_name=table_name))

    return app


def _has_db_creds() -> bool:
    return bool(session.get("db_user")) and bool(session.get("db_pass"))


def _test_connection(user: str, password: str) -> None:
    conn = psycopg2.connect(
        host=os.environ.get("DB_HOST", "db"),
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "software_company_db"),
        user=user,
        password=password,
        connect_timeout=3,
    )
    conn.close()


def _connect():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "db"),
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "software_company_db"),
        user=session["db_user"],
        password=session["db_pass"],
    )


def _get_columns(cur, table_name: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position;
        """,
        (table_name,),
    )
    return [r[0] for r in cur.fetchall()]


def _get_primary_key_column(cur, table_name: str) -> str | None:
    cur.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = 'public'
          AND tc.table_name = %s
        ORDER BY kcu.ordinal_position
        LIMIT 1;
        """,
        (table_name,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _get_column_data_type(cur, table_name: str, column_name: str) -> str | None:
    cur.execute(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s AND column_name=%s
        LIMIT 1;
        """,
        (table_name, column_name),
    )
    row = cur.fetchone()
    return row[0] if row else None


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool
    has_default: bool
    is_identity: bool
    is_generated: bool
    auto_generated: bool


def _get_columns_info(cur, table_name: str) -> list[ColumnInfo]:
    cur.execute(
        """
        SELECT
            c.column_name,
            c.data_type,
            (c.is_nullable = 'YES') AS nullable,
            (c.column_default IS NOT NULL) AS has_default,
            (c.is_identity = 'YES') AS is_identity,
            (c.is_generated = 'ALWAYS') AS is_generated,
            c.ordinal_position
        FROM information_schema.columns c
        WHERE c.table_schema = 'public' AND c.table_name = %s
        ORDER BY c.ordinal_position;
        """,
        (table_name,),
    )
    cols: list[ColumnInfo] = []
    for name, data_type, nullable, has_default, is_identity, is_generated, _pos in cur.fetchall():
        auto_generated = bool(has_default or is_identity or is_generated)
        cols.append(
            ColumnInfo(
                name=name,
                data_type=data_type,
                nullable=bool(nullable),
                has_default=bool(has_default),
                is_identity=bool(is_identity),
                is_generated=bool(is_generated),
                auto_generated=auto_generated,
            )
        )
    return cols


@dataclass(frozen=True)
class ForeignKeyInfo:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    constraint_name: str


def _get_outgoing_foreign_keys(cur, table_name: str) -> list[ForeignKeyInfo]:
    """
    FK из table_name → другие таблицы (проверка при INSERT).
    """
    cur.execute(
        """
        SELECT
            tc.table_name AS from_table,
            kcu.column_name AS from_column,
            ccu.table_name AS to_table,
            ccu.column_name AS to_column,
            tc.constraint_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public'
          AND tc.table_name = %s
        ORDER BY tc.constraint_name, kcu.ordinal_position;
        """,
        (table_name,),
    )
    return [
        ForeignKeyInfo(
            from_table=r[0],
            from_column=r[1],
            to_table=r[2],
            to_column=r[3],
            constraint_name=r[4],
        )
        for r in cur.fetchall()
    ]


def _get_incoming_foreign_keys(cur, table_name: str) -> list[ForeignKeyInfo]:
    """
    FK из других таблиц → table_name (проверка перед DELETE).
    """
    cur.execute(
        """
        SELECT
            kcu.table_name AS from_table,
            kcu.column_name AS from_column,
            ccu.table_name AS to_table,
            ccu.column_name AS to_column,
            tc.constraint_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public'
          AND ccu.table_name = %s
          AND ccu.table_schema = 'public'
        ORDER BY tc.constraint_name;
        """,
        (table_name,),
    )
    return [
        ForeignKeyInfo(
            from_table=r[0],
            from_column=r[1],
            to_table=r[2],
            to_column=r[3],
            constraint_name=r[4],
        )
        for r in cur.fetchall()
    ]


def _fk_target_exists(cur, fk: ForeignKeyInfo, value: Any) -> bool:
    cur.execute(
        sql.SQL("SELECT 1 FROM {t} WHERE {c} = %s LIMIT 1").format(
            t=sql.Identifier(fk.to_table),
            c=sql.Identifier(fk.to_column),
        ),
        (value,),
    )
    return cur.fetchone() is not None


def _get_unique_constraints(cur, table_name: str) -> dict[str, list[str]]:
    """
    Возвращает UNIQUE constraints как {constraint_name: [col1, col2, ...]}.
    """
    cur.execute(
        """
        SELECT tc.constraint_name, kcu.column_name, kcu.ordinal_position
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'public'
          AND tc.table_name = %s
          AND tc.constraint_type = 'UNIQUE'
        ORDER BY tc.constraint_name, kcu.ordinal_position;
        """,
        (table_name,),
    )
    result: dict[str, list[str]] = {}
    for constraint_name, column_name, _pos in cur.fetchall():
        result.setdefault(constraint_name, []).append(column_name)
    return result


def _coerce_value(raw: str, data_type: str) -> Any:
    dt = (data_type or "").lower()

    if dt in ("smallint", "integer", "bigint"):
        try:
            return int(raw)
        except ValueError:
            raise ValueError("ожидается целое число")

    if dt in ("numeric", "decimal", "real", "double precision"):
        try:
            return float(raw.replace(",", "."))
        except ValueError:
            raise ValueError("ожидается число")

    if dt == "boolean":
        v = raw.strip().lower()
        if v in ("true", "t", "1", "yes", "y", "да"):
            return True
        if v in ("false", "f", "0", "no", "n", "нет"):
            return False
        raise ValueError("ожидается boolean (true/false)")

    return raw


def _configure_logging(app: Flask) -> None:
    log_dir = os.environ.get("LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)

    user_log_path = os.path.join(log_dir, "logging.log")
    full_log_path = os.path.join(log_dir, "full_logging.log")

    user_logger = logging.getLogger("user")
    full_logger = logging.getLogger("full")

    user_logger.setLevel(logging.INFO)
    full_logger.setLevel(logging.DEBUG)

    # Чтобы при перезапуске gunicorn не плодить хендлеры
    if not any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == os.path.abspath(user_log_path) for h in user_logger.handlers):
        uh = logging.FileHandler(user_log_path, mode="a", encoding="utf-8")
        uh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
        user_logger.addHandler(uh)

    if not any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == os.path.abspath(full_log_path) for h in full_logger.handlers):
        fh = logging.FileHandler(full_log_path, mode="a", encoding="utf-8")
        fh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
        full_logger.addHandler(fh)

    # app.logger (Flask) пусть пишет в user-лог
    app.logger.handlers = []
    app.logger.propagate = False
    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(next(h for h in user_logger.handlers if isinstance(h, logging.FileHandler)))

    try:
        full_fh = next(h for h in full_logger.handlers if isinstance(h, logging.FileHandler))
        gunicorn_error = logging.getLogger("gunicorn.error")
        gunicorn_access = logging.getLogger("gunicorn.access")
        gunicorn_error.setLevel(logging.WARNING)
        gunicorn_access.setLevel(logging.INFO)

        for lg in (gunicorn_error, gunicorn_access):
            lg.propagate = False
            if not any(
                isinstance(h, logging.FileHandler)
                and getattr(h, "baseFilename", "") == getattr(full_fh, "baseFilename", "")
                for h in lg.handlers
            ):
                lg.addHandler(full_fh)
    except StopIteration:
        pass

app = create_app()

