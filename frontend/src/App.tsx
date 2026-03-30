import Container from '@mui/material/Container';
import { WhoWidget } from './components/WhoWidget';

function App() {
  return (
    <Container maxWidth="xl" sx={{ py: { xs: 2, md: 4 } }}>
      <WhoWidget />
    </Container>
  );
}

export default App;
