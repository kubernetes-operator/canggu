import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import './web-standard.css';
import './web-standard-auth.js';

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
