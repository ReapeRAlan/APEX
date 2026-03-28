import io
with io.open(r"d:\MACOV\APEX\frontend\src\App.tsx", "wb") as f:
    f.write(b"import React from 'react';\nimport MapView from './components/MapView';\nfunction App() { return <MapView />; }\nexport default App;")
