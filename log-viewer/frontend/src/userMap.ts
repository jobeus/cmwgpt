const userMapModules = import.meta.glob<Record<string, string>>('./user_map.json', {
    eager: true,
    import: 'default'
});

const userMap = Object.values(userMapModules)[0] || {};

export default userMap;