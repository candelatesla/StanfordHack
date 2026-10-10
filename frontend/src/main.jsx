import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import Soundboard from "./components/Soundboard.jsx";
import "./styles.css";

const Page = location.pathname.startsWith("/customer") ? Soundboard : App;
createRoot(document.getElementById("root")).render(<Page />);
