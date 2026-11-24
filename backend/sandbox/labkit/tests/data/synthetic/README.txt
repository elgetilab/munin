Synthetic CW-EPR spectra for labkit's tests (LABKIT-019), made with the
independent S8 simulator backend/benchmarks/fixtures/lab/generate/s8_powder.py
(exact diagonalisation for the powder cases, Breit-Rabi for the radical; its
orientation grid raised to COARSE=(241, 61), FINE=(3600, 1440), where it is
converged). New parameter values, not the eval fixtures. Each .npz holds
field_mT, y (first harmonic, noise added) and truth (JSON):
  cu_powder        natural Cu(II), g 2.058/2.244, A 35/575 MHz, g strain (0.004, 0.004, 0.010),
                   1.0 mT Gaussian, 0.4 mT modulation, SNR 250
  nitroxide_rigid  rigid 14N nitroxide, g (2.0083, 2.0060, 2.0022), A (0.61, 0.52, 3.38) mT,
                   0.6 mT Gaussian, 0.2 mT modulation, SNR 300
  radical          2 x 14N (0.712 mT) + 4 x 1H (0.218 mT), g 2.0035, Lorentzian dBpp 0.045 mT,
                   SNR 400
  rhombic          low-spin heme, g (1.921, 2.262, 2.431), g strain (0.030, 0.012, 0.020),
                   1.2 mT Gaussian, 0.6 mT modulation, SNR 200
