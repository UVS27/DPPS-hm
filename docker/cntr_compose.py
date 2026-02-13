import sys
import subprocess

COMPOSE_DIR = "docker"


def run_compose(args: list[str]) -> int:
    """Запускает docker compose ... в каталоге COMPOSE_DIR и возвращает код выхода."""
    try:
        proc = subprocess.run(
            ["docker", "compose", *args],
            cwd=COMPOSE_DIR
        )
        return proc.returncode
    except FileNotFoundError:
        print("Ошибка: docker не найден в PATH. Установите Docker Desktop и перезапустите терминал.")
        return 127
    except Exception as e:
        print(f"Не удалось выполнить docker compose: {e}")
        return 1


def is_any_container_running() -> bool:
    """Проверяет, есть ли запущенные контейнеры compose."""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "-q"],
            capture_output=True,
            text=True,
            cwd=COMPOSE_DIR
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


# -----------------------------
# Команды
# -----------------------------

def compose_build() -> None:
    print("Собираем образы...")
    code = run_compose(["build"])
    print("Сборка завершена." if code == 0 else f"Ошибка сборки. Код: {code}")


def compose_up() -> None:
    if is_any_container_running():
        print("Compose уже поднят.")
        return

    print("Поднимаем Compose...")
    code = run_compose(["up", "-d"])
    print("Compose поднят." if code == 0 else f"Не удалось поднять Compose. Код: {code}")


def compose_down() -> None:
    print("Полностью удаляем Compose (контейнеры, сеть)...")
    code = run_compose(["down"])
    print("Compose удалён." if code == 0 else f"Не удалось выполнить down. Код: {code}")


def compose_stop() -> None:
    if not is_any_container_running():
        print("Нет запущенных контейнеров.")
        return

    print("Останавливаем контейнеры (без удаления)...")
    code = run_compose(["stop"])
    print("Контейнеры остановлены." if code == 0 else f"Ошибка stop. Код: {code}")


def compose_start() -> None:
    print("Запускаем ранее остановленные контейнеры...")
    code = run_compose(["start"])
    print("Контейнеры запущены." if code == 0 else f"Ошибка start. Код: {code}")


# -----------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Укажите команду: U (up), D (down), B (build), S (stop), R (start)")
        sys.exit(1)

    action = sys.argv[1].upper()

    commands = {
        "U": compose_up,
        "D": compose_down,
        "B": compose_build,
        "S": compose_stop,
        "R": compose_start,
    }

    if action in commands:
        commands[action]()
    else:
        print("Неверный аргумент. Используйте U, D, B, S или R.")
        sys.exit(1)
