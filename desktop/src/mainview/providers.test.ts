import { describe, expect, it } from "vitest";
import { getProviderRequestOverrides } from "./providers";

describe("provider request overrides", () => {
	it("does not mask provider environment defaults", () => {
		expect(
			getProviderRequestOverrides(
				"openai",
				"https://api.openai.com/v1",
				"gpt-5.5",
			),
		).toEqual({ base_url: undefined, model: undefined });
	});

	it("keeps explicit custom endpoint and model", () => {
		expect(
			getProviderRequestOverrides(
				"openai",
				"https://relay.example/v1/",
				"relay-model",
			),
		).toEqual({ base_url: "https://relay.example/v1", model: "relay-model" });
	});

	it("supports custom provider values", () => {
		expect(
			getProviderRequestOverrides("custom", "http://localhost:9000/v1", "local-model"),
		).toEqual({ base_url: "http://localhost:9000/v1", model: "local-model" });
	});
});
