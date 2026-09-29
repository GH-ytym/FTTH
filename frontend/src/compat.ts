// Qt 5.15 embeds Chromium 87. Syntax transforms do not add runtime APIs.
if (!(Object as any).hasOwn)
  Object.defineProperty(Object, "hasOwn", {
    value: (object: object, key: PropertyKey) =>
      Object.prototype.hasOwnProperty.call(object, key),
    configurable: true,
    writable: true,
  });
