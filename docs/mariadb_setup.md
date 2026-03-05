# MariaDB Setup Guide

This guide describes how to configure the MariaDB server for the CMWGPT bot.

## 1. Access the MariaDB Console

If you are running MariaDB on your server natively, SSH into your server and run:

```bash
mysql -u root -p
```
*(Enter your root password when prompted)*

## 2. Create the Database

Create the `cmwgpt` database with proper character encoding for emojis and international text:

```sql
CREATE DATABASE IF NOT EXISTS cmwgpt CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

## 3. Create the Database User

Create a dedicated user for the bot. Replace `'your_secure_password'` with a strong password.

```sql
CREATE USER 'cmwgpt_user'@'localhost' IDENTIFIED BY 'your_secure_password';
```

## 4. Grant Privileges

Give the new user all necessary permissions on the `cmwgpt` database:

```sql
GRANT ALL PRIVILEGES ON cmwgpt.* TO 'cmwgpt_user'@'localhost';
FLUSH PRIVILEGES;
```

## 5. Initialize the Schema

Exit the MariaDB console:
```sql
EXIT;
```

Now, import the provided database schema:
```bash
# Assuming you are in the cmwgpt project directory on the server
mysql -u cmwgpt_user -p cmwgpt < init_db.sql
```
*(Enter the password you created in Step 3)*

## 6. Update `.env`

Update your `.env` file in the project directory to include these credentials:

```ini
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=cmwgpt_user
DB_PASSWORD=your_secure_password
DB_NAME=cmwgpt
```
