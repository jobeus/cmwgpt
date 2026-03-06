import mariadb from 'mariadb';
import dotenv from 'dotenv';
import path from 'path';

// Load the root .env file from two directories up
dotenv.config({ path: path.join(__dirname, '../../../.env') });

export const pool = mariadb.createPool({
    host: process.env.DB_HOST || 'localhost',
    user: process.env.DB_USER || 'root',
    password: process.env.DB_PASSWORD || '',
    database: process.env.DB_NAME || 'cmwgpt',
    connectionLimit: 5,
    multipleStatements: true,
    bigIntAsNumber: true, // important for bigints like user ids if they fit JS Number, though strings are safer. We will handle safely.
});

export const getConnection = async () => {
    return await pool.getConnection();
};
