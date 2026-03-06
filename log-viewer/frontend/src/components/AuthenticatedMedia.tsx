import { useEffect, useState, type ReactNode } from 'react';
import axios from 'axios';
import { proxyMediaUrl } from '../utils/media';

const useAuthenticatedMediaUrl = (src: string, stripQuery: boolean) => {
    const [mediaUrl, setMediaUrl] = useState<string | null>(null);
    const [failed, setFailed] = useState(false);

    useEffect(() => {
        if (!src) {
            setMediaUrl(null);
            setFailed(false);
            return;
        }

        if (src.startsWith('data:') || src.startsWith('blob:')) {
            setMediaUrl(src);
            setFailed(false);
            return;
        }

        let objectUrl: string | null = null;
        let cancelled = false;

        setMediaUrl(null);
        setFailed(false);

        axios.get(proxyMediaUrl(src, stripQuery), { responseType: 'blob' })
            .then(({ data }) => {
                if (cancelled) {
                    return;
                }
                objectUrl = URL.createObjectURL(data);
                setMediaUrl(objectUrl);
            })
            .catch((error) => {
                if (!cancelled) {
                    console.error('Failed to load proxied media:', error);
                    setFailed(true);
                }
            });

        return () => {
            cancelled = true;
            if (objectUrl) {
                URL.revokeObjectURL(objectUrl);
            }
        };
    }, [src, stripQuery]);

    return { mediaUrl, failed };
};

interface AuthenticatedImageProps {
    src: string;
    alt: string;
    className?: string;
    stripQuery?: boolean;
    fallback?: ReactNode;
    loadingFallback?: ReactNode;
}

export const AuthenticatedImage = ({ src, alt, className, stripQuery = true, fallback, loadingFallback }: AuthenticatedImageProps) => {
    const { mediaUrl, failed } = useAuthenticatedMediaUrl(src, stripQuery);

    if (failed) {
        return <>{fallback ?? <a href={src} target="_blank" rel="noreferrer" className="text-blue-400 hover:underline">Open image</a>}</>;
    }

    if (!mediaUrl) {
        return <>{loadingFallback ?? <div className={className} />}</>;
    }

    return <img src={mediaUrl} alt={alt} className={className} />;
};

interface AuthenticatedVideoProps {
    src: string;
    className?: string;
    stripQuery?: boolean;
    type?: string;
    fallback?: ReactNode;
    loadingFallback?: ReactNode;
}

export const AuthenticatedVideo = ({ src, className, stripQuery = true, type = 'video/mp4', fallback, loadingFallback }: AuthenticatedVideoProps) => {
    const { mediaUrl, failed } = useAuthenticatedMediaUrl(src, stripQuery);

    if (failed) {
        return <>{fallback ?? <a href={src} target="_blank" rel="noreferrer" className="text-blue-400 hover:underline">Open video</a>}</>;
    }

    if (!mediaUrl) {
        return <>{loadingFallback ?? <div className={className} />}</>;
    }

    return (
        <video controls className={className} preload="metadata">
            <source src={mediaUrl} type={type} />
            Your browser does not support the video tag.
        </video>
    );
};