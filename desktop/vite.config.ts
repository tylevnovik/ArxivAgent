import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
	plugins: [react()],
	base: "./",
	root: "src/mainview",
	build: {
		outDir: "../../dist",
		emptyOutDir: true,
		// 拆分大型 vendor，消除单 chunk > 500KB 警告，首屏可并行加载。
		rollupOptions: {
			output: {
				manualChunks: {
					react: ["react", "react-dom"],
					mui: ["@mui/material", "@mui/icons-material", "@emotion/react", "@emotion/styled"],
					assistantui: ["@assistant-ui/react"],
					markdown: ["marked", "dompurify"],
				},
			},
		},
		chunkSizeWarningLimit: 600,
	},
	server: {
		port: 5173,
		strictPort: true,
	},
});
