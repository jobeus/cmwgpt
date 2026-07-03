import './config';
import mariadb from 'mariadb';

export const pool = mariadb.createPool({
    host: process.env.DB_HOST || 'localhost',
    user: process.env.DB_USER || 'root',
    password: process.env.DB_PASSWORD || '',
    database: process.env.DB_NAME || 'cmwgpt',
    connectionLimit: 5,
    bigIntAsNumber: true, // important for bigints like user ids if they fit JS Number, though strings are safer. We will handle safely.
});
