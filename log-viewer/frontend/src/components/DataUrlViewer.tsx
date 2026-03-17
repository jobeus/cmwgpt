

export const DataUrlViewer = ({ dataUrl }: { dataUrl: string }) => {
    // Check type
    const isImage = dataUrl.startsWith('data:image/');
    const isAudio = dataUrl.startsWith('data:audio/');
    const isVideo = dataUrl.startsWith('data:video/');

    if (isImage) {
        return (
            <div className="relative group p-2 mb-2 bg-gray-900 rounded-lg border border-gray-800 inline-block max-w-full">
               <img src={dataUrl} alt="Data URL Content" className="max-w-full max-h-[60vh] object-contain rounded" />
            </div>
        );
    }

    if (isAudio) {
        return (
            <div className="relative p-2 mb-2 bg-gray-900 rounded-lg border border-gray-800">
               <audio controls src={dataUrl} className="w-full" />
            </div>
        );
    }

    if (isVideo) {
        return (
            <div className="relative p-2 mb-2 bg-gray-900 rounded-lg border border-gray-800">
               <video controls src={dataUrl} className="max-w-full max-h-[60vh] object-contain rounded" />
            </div>
        );
    }

    // Fallback: Just show the data URL text with an alert that it's unrecognized binary
    return null;
};
