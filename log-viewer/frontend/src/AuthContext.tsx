import React, { createContext, useContext, useState, useEffect } from 'react';
import axios from 'axios';

interface AuthContextType {
    token: string | null;
    username: string | null;
    login: (token: string, username: string) => void;
    logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

// Set default header synchronously before any component mounts
const initialToken = localStorage.getItem('token');
if (initialToken) {
    axios.defaults.headers.common['Authorization'] = `Bearer ${initialToken}`;
}

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [token, setToken] = useState<string | null>(initialToken);
    const [username, setUsername] = useState<string | null>(localStorage.getItem('username'));

    useEffect(() => {
        if (token) {
            axios.defaults.headers.common['Authorization'] = `Bearer ${token}`;
        } else {
            delete axios.defaults.headers.common['Authorization'];
        }
    }, [token]);

    const login = (newToken: string, newUsername: string) => {
        setToken(newToken);
        setUsername(newUsername);
        localStorage.setItem('token', newToken);
        localStorage.setItem('username', newUsername);
    };

    const logout = () => {
        setToken(null);
        setUsername(null);
        localStorage.removeItem('token');
        localStorage.removeItem('username');
    };

    return (
        <AuthContext.Provider value={{ token, username, login, logout }}>
            {children}
        </AuthContext.Provider>
    );
};

export const useAuth = () => {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
};
