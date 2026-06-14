function isEncryptionAvailable(safeStorage) {
	try {
		return Boolean(safeStorage?.isEncryptionAvailable?.());
	} catch {
		return false;
	}
}

function encodeSecretEntry(value, safeStorage) {
	const secret = typeof value === "string" ? value : String(value ?? "");
	if (!secret) return null;

	if (isEncryptionAvailable(safeStorage)) {
		const encrypted = safeStorage.encryptString(secret);
		return {
			encrypted: true,
			encoding: "base64",
			value: Buffer.from(encrypted).toString("base64"),
		};
	}

	return { encrypted: false, value: secret };
}

function decodeSecretEntry(entry, safeStorage) {
	if (!entry || typeof entry !== "object") return null;

	if (entry.encrypted) {
		if (!isEncryptionAvailable(safeStorage)) return null;
		if (typeof entry.value !== "string" || !entry.value) return null;
		return safeStorage.decryptString(Buffer.from(entry.value, "base64"));
	}

	return typeof entry.value === "string" ? entry.value : null;
}

module.exports = {
	decodeSecretEntry,
	encodeSecretEntry,
	isEncryptionAvailable,
};
