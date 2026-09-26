import React from 'react';
import ReactDOM from 'react-dom/client';
import CostTheme from './CostTheme';
import CostApp from './CostApp';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><CostTheme><CostApp /></CostTheme></React.StrictMode>,
);
