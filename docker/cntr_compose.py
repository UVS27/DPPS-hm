import sys
import subprocess

COMPOSE_DIR = "docker"

def is_compose_up():
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "-q"],
            capture_output=True, text=True,
            cwd=COMPOSE_DIR
        )
        return bool(result.stdout.strip())
    except:
        return False

def compose_up():
    if is_compose_up():
        print("Docker Compose уже поднят.")
    else:
        print("Поднимаем Docker Compose...")
        subprocess.run(["docker", "compose", "up", "-d"], cwd=COMPOSE_DIR)

def compose_down():
    if not is_compose_up():
        print("Docker Compose уже опущен.")
    else:
        print("Останавливаем Docker Compose...")
        subprocess.run(["docker", "compose", "down"], cwd=COMPOSE_DIR)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Укажите U для up или D для down")
        sys.exit(1)
    action = sys.argv[1].upper()
    if action == "U":
        compose_up()
    elif action == "D":
        compose_down()
    else:
        print("Неверный аргумент. Используйте U или D")