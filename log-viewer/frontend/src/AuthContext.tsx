import React, { createContext, useContext, useState, useEffect } from 'react';
import axios from 'axios';

interface AuthContextType {
    token: string | null;
    username: string | null;
    login: (token: string, username: string) => void;
    logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const TOKEN_STORAGE_KEY = 'log-viewer.token';
const USERNAME_STORAGE_KEY = 'log-viewer.username';

const getInitialStoredValue = (sessionKey: string, legacyLocalStorageKey: string) => {
    const sessionValue = sessionStorage.getItem(sessionKey);
    if (sessionValue) {
        return sessionValue;
    }

    const legacyValue = localStorage.getItem(legacyLocalStorageKey);
    if (legacyValue) {
        sessionStorage.setItem(sessionKey, legacyValue);
        localStorage.removeItem(legacyLocalStorageKey);
        return legacyValue;
    }

    return null;
};

// Set default header synchronously before any component mounts
const initialToken = getInitialStoredValue(TOKEN_STORAGE_KEY, 'token');
if (initialToken) {
    axios.defaults.headers.common['Authorization'] = `Bearer ${initialToken}`;
}

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [token, setToken] = useState<string | null>(initialToken);
    const [username, setUsername] = useState<string | null>(getInitialStoredValue(USERNAME_STORAGE_KEY, 'username'));

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
        sessionStorage.setItem(TOKEN_STORAGE_KEY, newToken);
        sessionStorage.setItem(USERNAME_STORAGE_KEY, newUsername);
        localStorage.removeItem('token');
        localStorage.removeItem('username');
    };

    const logout = () => {
        setToken(null);
        setUsername(null);
        sessionStorage.removeItem(TOKEN_STORAGE_KEY);
        sessionStorage.removeItem(USERNAME_STORAGE_KEY);
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
