import mysql.connector
from flask_bcrypt import Bcrypt
from flask import Flask

app = Flask(__name__)
bcrypt = Bcrypt(app)

db_config = {
    'host': 'localhost',
    'user': 'job',
    'password': 'Xdman123456@',
    'database': 'job'
}

try:
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()
    
    # Check if admin user exists
    cursor.execute("SELECT id FROM admins WHERE username = 'admin'")
    result = cursor.fetchone()
    
    password = 'password'
    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
    
    if result:
        # Update existing admin
        cursor.execute("UPDATE admins SET password = %s WHERE username = 'admin'", (hashed_password,))
        print("Admin user found. Password reset to: 'password'")
    else:
        # Insert new admin
        cursor.execute('INSERT INTO admins (username, password, name, email) VALUES (%s, %s, %s, %s)',
                     ('admin', hashed_password, 'Administrator', 'admin@example.com'))
        print("Admin user created. Username: 'admin', Password: 'password'")
        
    conn.commit()
    cursor.close()
    conn.close()
    
except Exception as e:
    print(f"Error: {e}")
