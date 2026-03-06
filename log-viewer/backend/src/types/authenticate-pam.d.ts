declare module 'authenticate-pam' {
    export function authenticate(
        username: string,
        password?: string,
        callback?: (err?: string | Error) => void
    ): void;

    export function authenticate(
        username: string,
        password?: string,
        options?: any,
        callback?: (err?: string | Error) => void
    ): void;
}
