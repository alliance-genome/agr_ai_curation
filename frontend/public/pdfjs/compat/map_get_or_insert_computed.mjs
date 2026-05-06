if (typeof Map.prototype.getOrInsertComputed !== "function") {
  Object.defineProperty(Map.prototype, "getOrInsertComputed", {
    configurable: true,
    writable: true,
    value(key, callback) {
      if (typeof callback !== "function") {
        throw new TypeError("Map.prototype.getOrInsertComputed callback must be a function");
      }

      if (this.has(key)) {
        return this.get(key);
      }

      const value = callback(key);
      this.set(key, value);
      return value;
    },
  });
}
