import { useEffect, useState } from "react";

const MOBILE_MAX_WIDTH = 767;

export function isMobileWidth() {
  return window.innerWidth > 0 && window.innerWidth <= MOBILE_MAX_WIDTH;
}

export function useIsMobile() {
  const [mobile, setMobile] = useState(isMobileWidth);

  useEffect(() => {
    const handler = () => setMobile(isMobileWidth());
    window.addEventListener("resize", handler);
    handler();
    return () => window.removeEventListener("resize", handler);
  }, []);

  return mobile;
}