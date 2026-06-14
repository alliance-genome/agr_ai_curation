if (typeof Uint8Array.prototype.toHex !== "function") {
  Object.defineProperty(Uint8Array.prototype, "toHex", {
    configurable: true,
    writable: true,
    value() {
      let hex = "";
      for (let i = 0; i < this.length; i++) {
        hex += this[i].toString(16).padStart(2, "0");
      }
      return hex;
    },
  });
}
