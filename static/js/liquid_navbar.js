/* ==========================================================================
   FRAMER-INSPIRED LIQUID GLASS NAVBAR CONTROLLER (Liquid-Glass-Navbar)
   ========================================================================== */

document.addEventListener('DOMContentLoaded', () => {
  const navbar = document.querySelector('.liquid-navbar');
  const navLinks = document.querySelectorAll('.nav-link');
  const currentPath = window.location.pathname;

  // Highlight current active link based on URL
  navLinks.forEach(link => {
    const href = link.getAttribute('href');
    if (href === currentPath || (href !== '/' && currentPath.startsWith(href))) {
      link.classList.add('active');
    } else {
      link.classList.remove('active');
    }
  });

  // Dynamic glass blur tilt effect on scroll
  window.addEventListener('scroll', () => {
    if (window.scrollY > 20) {
      navbar.style.background = 'rgba(15, 23, 42, 0.85)';
      navbar.style.borderColor = 'rgba(56, 189, 248, 0.3)';
    } else {
      navbar.style.background = 'rgba(15, 23, 42, 0.65)';
      navbar.style.borderColor = 'rgba(255, 255, 255, 0.15)';
    }
  });
});
