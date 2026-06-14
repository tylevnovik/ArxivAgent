import { describe, expect, it } from "vitest";

const {
	decodeSecretEntry,
	encodeSecretEntry,
	isEncryptionAvailable,
} = require("./secrets-store.cjs");

function fakeSafeStorage(available = true) {
	return {
		isEncryptionAvailable: () => available,
		encryptString: (value: string) => Buffer.from(`sealed:${value}`, "utf-8"),
		decryptString: (value: Buffer) => {
			const text = value.toString("utf-8");
			if (!text.startsWith("sealed:")) {
				throw new Error("invalid ciphertext");
			}
			return text.slice("sealed:".length);
		},
	};
}

describe("electron secrets-store", () => {
	it("round-trips encrypted entries through base64 encoding", () => {
		const safeStorage = fakeSafeStorage(true);
		const entry = encodeSecretEntry("sk-test-token", safeStorage);

		expect(entry).toMatchObject({ encrypted: true, encoding: "base64" });
		expect(entry.value).not.toContain("sk-test-token");
		expect(decodeSecretEntry(entry, safeStorage)).toBe("sk-test-token");
	});

	it("returns null for encrypted entries when encryption is unavailable", () => {
		const entry = encodeSecretEntry("sk-test-token", fakeSafeStorage(true));
		expect(decodeSecretEntry(entry, fakeSafeStorage(false))).toBeNull();
	});

	it("falls back to plaintext entries when encryption is unavailable", () => {
		const safeStorage = fakeSafeStorage(false);
		const entry = encodeSecretEntry("sk-dev-token", safeStorage);

		expect(entry).toEqual({ encrypted: false, value: "sk-dev-token" });
		expect(decodeSecretEntry(entry, safeStorage)).toBe("sk-dev-token");
	});

	it("treats safeStorage errors as unavailable", () => {
		expect(isEncryptionAvailable({
			isEncryptionAvailable: () => {
				throw new Error("no keyring");
			},
		})).toBe(false);
	});
});
