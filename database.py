import sqlite3

# Название файла базы данных (будет создан автоматически при запуске)
DB_NAME = "app_database.db"

def get_connection():
    """Создает и возвращает подключение к базе данных."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # Позволяет обращаться к колонкам по имени
    return conn

def init_db():
    """Инициализирует базу данных и создает необходимые таблицы."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Пример создания таблицы пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
    print("База данных успешно инициализирована.")

if __name__ == "__main__":
    init_db()
