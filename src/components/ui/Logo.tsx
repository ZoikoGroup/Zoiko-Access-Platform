import Image from "next/image";

type LogoProps = {
  /** "auto" shows the navy logo in light mode and the white one in dark mode. */
  variant?: "auto" | "color" | "white";
  className?: string;
  priority?: boolean;
};

const color = { src: "/brand/zoiko-logo.png", width: 964, height: 343 };
const white = { src: "/brand/zoiko-logo-white.png", width: 1213, height: 432 };

export default function Logo({ variant = "auto", className = "h-12 w-auto", priority }: LogoProps) {
  if (variant !== "auto") {
    const img = variant === "white" ? white : color;
    return <Image {...img} alt="Zoiko Access" className={className} priority={priority} />;
  }
  return (
    <>
      <Image {...color} alt="Zoiko Access" className={`${className} dark:hidden`} priority={priority} />
      <Image {...white} alt="Zoiko Access" className={`${className} hidden dark:block`} priority={priority} />
    </>
  );
}
